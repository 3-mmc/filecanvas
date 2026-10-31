"""Deterministic Thunderbird integration; all mail and identities here are synthetic."""
import json
import sys
import threading
from unittest.mock import patch
import pytest
from twigs import core as fc
from twigs.apps.thunderbird.connection import Client
from twigs.apps.thunderbird.canvas import Thunderbird, folders, threads, groups
from twigs.apps.thunderbird import settings

ACCOUNTS = [dict(id='a1', name='Existing', identities=[dict(email='Reader@Example.test')]),
            dict(id='a2', name='New mail', identities=[dict(email='other@example.test')])]
FOLDER = dict(name='Inbox', path='mailbox://test/Inbox', depth=0, accountId='a2', unreadMessages=1)
MESSAGE = dict(id='one@example.test', subject='Subject', author='Sender', date='2026-10-30T10:00:00Z',
               folderPath=FOLDER['path'], read=False)
BODY = dict(MESSAGE, author='Sender <sender@example.test>', body='Hello\x1b[31m world', recipients='other@example.test', attachments=[dict(name='a.txt', size=12)])


def fake_bridge(mode='normal'):
    script = '''
import sys,json,time
for line in sys.stdin:
 p=json.loads(line)
 if 'id' not in p: continue
 if p['method']=='initialize': r={'serverInfo':{'name':'fixture'},'protocolVersion':'2025-11-25','capabilities':{}}
 else:
  MODE
  r={'content':[{'type':'text','text':json.dumps([{'id':'a1'}])}]}
 print(json.dumps({'jsonrpc':'2.0','id':p['id'],'result':r}),flush=True)
'''
    action = {'normal':'pass', 'timeout':'time.sleep(5)',
              'error':"print(json.dumps({'jsonrpc':'2.0','id':p['id'],'result':{'isError':True,'content':[{'type':'text','text':'Account access denied'}]}}),flush=True);continue",
              'badid':"p['id']+=1"}[mode]
    return [sys.executable, '-u', '-c', script.replace('MODE',action)]


def test_real_stdio_lifecycle_parses_tools_and_closes_only_its_bridge():
    c = Client(command=fake_bridge())
    assert c.call('listAccounts') == [{'id':'a1'}]
    process = c.process
    c.close()
    assert process.poll() is not None


@pytest.mark.parametrize('mode,error', [('timeout','timed out'), ('error','access denied'), ('badid','response ID')])
def test_protocol_failure_is_actionable_and_bounded(mode,error):
    with Client(command=fake_bridge(mode),timeout=0.05) as c:
        with pytest.raises(OSError,match=error): c.call('listAccounts')


def test_write_tools_are_refused_before_starting_a_process():
    c=Client()
    with pytest.raises(ValueError): c.call('sendMail',{'to':'nobody@example.test'})
    assert c.process is None


def test_overlap_detection_uses_all_identities_case_insensitively_without_alias_guessing():
    selected,skipped=settings.choose(ACCOUNTS, addresses={'reader@example.test'})
    assert selected==['a2'] and skipped==[ACCOUNTS[0]]
    assert settings.choose(ACCOUNTS,['a1'],True,{'reader@example.test'})[0]==['a1']
    assert settings.choose(ACCOUNTS,addresses={'reader+tag@example.test'})[0]==['a1','a2']
    with pytest.raises(ValueError,match='Unknown'): settings.choose(ACCOUNTS,['absent'],addresses=set())


def test_configuration_preserves_explicit_account_choices_without_tokens(tmp_path,monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME',str(tmp_path))
    data=dict(accounts=['a2'],node='/test/node',include_existing=False)
    settings.save(data)
    assert settings.load()==data
    assert settings.path().stat().st_mode & 0o777==0o600


def test_folders_are_listed_and_ordered_as_the_native_emails_by_type_not_language():
    rows=[dict(FOLDER,name='[Gmail]',depth=0,type='folder',path='g'),
          dict(FOLDER,name='Papierkorb',depth=1,type='trash',path='g/t'),
          dict(FOLDER,name='Gesendet',depth=1,type='sent',path='g/s'),
          dict(FOLDER,name='Entwürfe',depth=1,type='drafts',path='g/d'),
          dict(FOLDER,name='Projekte',depth=0,type='folder',path='p'),
          dict(FOLDER,name='Alt',depth=1,type='folder',path='p/a'),
          dict(FOLDER,name='INBOX',depth=0,type='inbox',path='i')]
    got=folders(rows)
    assert [f['rel'] for f in got]==['INBOX','[Gmail]/Gesendet','[Gmail]/Entwürfe','[Gmail]/Papierkorb','Projekte','Projekte/Alt']


def test_messages_become_threads_and_groups_by_date_or_sender():
    now=1793404800          # 2026-10-31, a Saturday
    day=lambda d:f'2026-10-{d:02d}T10:00:00Z'
    ms=[dict(MESSAGE,id='a',threadId=1,date=day(31),author='Ann <ann@example.test>',read=True),
        dict(MESSAGE,id='b',threadId=1,date=day(30),author='Bo <bo@example.test>',read=False),
        dict(MESSAGE,id='c',threadId=2,date=day(3),author='Cy <cy@example.test>',read=True,flagged=True)]
    ts=threads(ms)
    assert [t['count'] for t in ts]==[2,1] and ts[0]['unread'] and ts[0]['author']=='Bo' and ts[1]['flagged']
    assert [g for g,_ in groups(ts,'date',now)][0] in ('Today','Yesterday')
    assert [g for g,_ in groups(ts,'sender',now)]==['Bo','Cy']


class Fixture:
    def __init__(self,config): self.closed=False;self.calls=[]
    def call(self,name,args=None):
        self.calls.append((name,args))
        if name=='listAccounts': return ACCOUNTS
        if name=='listFolders': return [FOLDER]
        if name=='getMessage': return BODY
        offset=args['offset']
        return dict(messages=[MESSAGE] if not offset else [dict(MESSAGE,id='two@example.test')],
                    offset=offset,limit=1,hasMore=not offset,truncated=bool(offset),message='Narrow search')
    def close(self): self.closed=True


def loaded(p,n):
    n.load();p.wait();n.kids=None;return n.load()


def test_canvas_is_drawn_as_the_native_emails_with_pagination_and_overlap_filtering():
    with patch.object(settings,'existing_addresses',return_value={'reader@example.test'}):
        p=Thunderbird(dict(accounts=['a1','a2']),client_factory=Fixture,grouping='all')
        try:
            account=loaded(p,p.root())[0]
            assert account.data['id']=='a2'
            folder=loaded(p,account)[0]
            assert folder.name=='Inbox' and p.count(folder)[1]=='git_dirty'
            first=loaded(p,folder)
            thread=first[0]
            assert thread.kind=='tb-thread' and thread.name=='Subject' and p.mark(thread)[1]=='●' and p.mark_before(thread)
            more=first[-1];assert more.kind=='tb-more'
            p.choose(more);p.wait();folder.kids=None
            page=folder.load()
            assert len([n for n in page if n.kind=='tb-thread'])==2
            assert any(n.name=='Narrow search' for n in page)
            children=loaded(p,thread)
            assert [n.kind for n in children][:4]==['tb-subject','tb-person','tb-person','tb-when']
            body=next(n for n in children if n.kind=='tb-body')
            assert '\x1b' not in body.name
            sender=next(n for n in children if n.kind=='tb-person')
            assert p.count(sender)[0]=='from  sender@example.test'
            assert next(n for n in children if n.kind=='tb-attachment').name=='📎 a.txt'
            you=next(n for n in children if n.kind=='tb-person' and n.data['role']=='to')
            assert p.chip(you)==('home_fg','home_bg')
            args=next(a for n,a in p.client.calls if n=='getMessage')
            assert args['bodyFormat']=='text' and args['saveAttachments'] is False
            assert p.reference(body)['priority']==0
        finally: p.close()
        assert p.client.closed


def test_folder_threads_group_by_date_then_sender_with_ctrl_l():
    p=Thunderbird(dict(accounts=['a2']),client_factory=Fixture)
    folder=fc.Node('/thunderbird/f','Inbox',True,source=p,kind='tb-folder',data=dict(FOLDER,rel='INBOX'))
    class Canvas:
        def say(self,text):self.said=text
    try:
        group=loaded(p,folder)[0]
        assert group.kind=='tb-group' and p.count(group)==('1','stub',False) and p.mark(group)[1]=='●'
        assert group.load()[0].kind=='tb-thread'
        c=Canvas();p.act(group,'group',c);folder.kids=None
        assert c.said=='by sender' and folder.load()[0].name=='Sender'
        assert ('\x0c','group','⌃L not grouped') in p.actions(folder)
    finally:p.close()


def test_every_chip_is_a_colour_each_theme_has():
    p=Thunderbird(dict(accounts=['a2']),client_factory=Fixture)
    try:
        person=fc.Node('/x','Sender',False,source=p,kind='tb-person',data=dict(role='from',address='sender@example.test'))
        for theme in fc.THEMES.values():
            paint=fc.Paint(theme,True)
            paint.on(*p.chip(person))
            for _,fg,bg,_ in p.bar(person,False)[0]+p.bar(person,False)[1]:
                paint.on(fg,bg)
    finally:p.close()


def test_new_query_cannot_be_overwritten_by_an_old_inflight_page():
    entered,release=threading.Event(),threading.Event()
    class Slow(Fixture):
        def call(self,name,args=None):
            if name=='searchMessages':
                if not args['query']:
                    entered.set();release.wait(2)
                return dict(messages=[dict(MESSAGE,subject=args['query'])],offset=0,limit=50,hasMore=False)
            return super().call(name,args)
    p=Thunderbird(dict(accounts=['a2']),client_factory=Slow)
    folder=fc.Node('/thunderbird/f','Inbox',True,source=p,kind='tb-folder',data=FOLDER)
    try:
        p.request(folder);assert entered.wait(2)
        p.queries[folder.path]='new query';p.request(folder,force=True);release.set();p.wait()
        assert p.snapshots[folder.path]['messages'][0]['subject']=='new query'
    finally: release.set();p.close()


def test_existing_addresses_read_only_includes_manual_accounts():
    from types import SimpleNamespace
    from twigs.apps.msg import accounts
    with patch.object(accounts,'load',return_value=({},[SimpleNamespace(address='Reader@Example.test')])):
        assert settings.existing_addresses()=={'reader@example.test'}


def test_configuration_errors_do_not_disable_duplicate_detection():
    from twigs.apps.msg import accounts
    with patch.object(accounts,'load',side_effect=ValueError('broken mail config')):
        with pytest.raises(ValueError,match='broken mail'): settings.existing_addresses()


def test_upstream_content_withheld_notice_is_displayed_without_overriding_privacy():
    class Withheld(Fixture):
        def call(self,name,args=None):
            if name=='getMessage':
                return dict(BODY, body='[Message content withheld: open it in Thunderbird.]',encryptedContentWithheld=True)
            return super().call(name,args)
    p=Thunderbird(dict(accounts=['a2']),client_factory=Withheld)
    n=fc.Node('/fixture','Message',True,source=p,kind='tb-message',data=dict(MESSAGE,stamp=0))
    try:
        rows=loaded(p,n)
        assert any('Message content withheld' in r.name for r in rows)
    finally:p.close()


@pytest.mark.parametrize('version,ok',[('v18.0.0',True),('v24.0.0',True),('v17.0.0',False),('broken',False)])
def test_doctor_checks_runtime_without_reading_a_thunderbird_session(version,ok):
    import subprocess
    from twigs.kit import doctor
    with patch.object(settings,'load',return_value=dict(accounts=['a2'],node='/test/node.exe')), \
         patch.object(doctor.subprocess,'run',return_value=subprocess.CompletedProcess([],0,version,'')), \
         patch('twigs.apps.thunderbird.host.chosen',return_value=None), \
         patch('twigs.apps.thunderbird.host.where',return_value='linux'):
        report=doctor.diagnose(['thunderbird'],'ubuntu',command=lambda n:'/test/node.exe')
    assert next(c['ok'] for c in report['checks'] if c['name']=='Node.js 18+') is ok


def test_preferred_thunderbird_source_hides_manual_duplicate_but_preserves_config():
    from types import SimpleNamespace
    existing=SimpleNamespace(address='Reader@Example.test',key='reader')
    other=SimpleNamespace(address='other@example.test',key='other')
    config=dict(accounts=['a1'],include_existing=False,
                preferred_accounts={'a1':['reader@example.test'],'not-selected':['other@example.test']})
    with patch.object(settings,'load',return_value=config), \
         patch.object(settings,'existing_addresses',return_value={'reader@example.test'}):
        assert settings.selection(ACCOUNTS,config)[0]==['a1']
        assert settings.manual_accounts([existing,other])==[other]
    assert existing.address=='Reader@Example.test'


def test_local_folders_are_explicit_instead_of_the_only_default_connection():
    local=dict(id='local',name='Local Folders',type='none',identities=[])
    assert settings.choose(ACCOUNTS+[local],addresses={'reader@example.test','other@example.test'})[0]==[]
    assert settings.choose([local],requested=['local'],addresses=set())[0]==['local']


def test_sync_of_thunderbird_preferred_account_never_calls_oauth_or_mbsync():
    from types import SimpleNamespace
    from twigs.apps.msg import cli
    account=SimpleNamespace(name='Reader',key='reader',address='reader@example.test')
    with patch.object(cli,'configured',return_value=[account]), \
         patch.object(settings,'preferred_addresses',return_value={'reader@example.test'}), \
         patch.object(cli.subprocess,'call') as call:
        with pytest.raises(SystemExit,match='uses Thunderbird'):
            cli.sync('Reader')
        call.assert_not_called()


def test_sync_all_runs_only_manual_accounts_and_propagates_failure():
    from types import SimpleNamespace
    from twigs.apps.msg import cli
    aa=[SimpleNamespace(name='Reader',key='reader',address='reader@example.test'),
        SimpleNamespace(name='Other',key='other',address='other@example.test')]
    with patch.object(cli,'configured',return_value=aa), \
         patch.object(settings,'preferred_addresses',return_value={'reader@example.test'}), \
         patch.object(cli,'need'), \
         patch.object(cli.subprocess,'call',side_effect=[1,0]) as call:
        with pytest.raises(SystemExit) as result:cli.sync()
        assert result.value.code==1
        assert call.call_args_list[0].args[0]==['mbsync','-c',cli.accounts.MBSYNCRC,'other']


def test_connect_preference_is_saved_from_live_identities_without_creating_manual_accounts(capsys):
    from twigs.apps.thunderbird import cli
    class Live(Fixture):
        def __enter__(self):return self
        def __exit__(self,*args):self.close()
    with patch.object(cli,'Client',Live), \
         patch.object(settings,'load',return_value=None), \
         patch.object(settings,'existing_addresses',return_value={'reader@example.test'}), \
         patch.object(settings,'save') as save, \
         patch('twigs.kit.apps.keep'):
        cli.main(['connect','--account','a1','--prefer-thunderbird'])
        config=save.call_args.args[0]
        assert config['accounts']==['a1']
        assert config['preferred_accounts']=={'a1':['reader@example.test']}
        assert config['include_existing'] is False
