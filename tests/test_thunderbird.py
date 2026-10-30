"""Deterministic Thunderbird integration; all mail and identities here are synthetic."""
import json
import sys
import threading
from unittest.mock import patch
import pytest
from twigs import core as fc
from twigs.apps.thunderbird.connection import Client
from twigs.apps.thunderbird.canvas import Thunderbird, folder_tree
from twigs.apps.thunderbird import settings

ACCOUNTS = [dict(id='a1', name='Existing', identities=[dict(email='Reader@Example.test')]),
            dict(id='a2', name='New mail', identities=[dict(email='other@example.test')])]
FOLDER = dict(name='Inbox', path='mailbox://test/Inbox', depth=0, accountId='a2', unreadMessages=1)
MESSAGE = dict(id='one@example.test', subject='Subject', author='Sender', date='2026-10-30T10:00:00Z',
               folderPath=FOLDER['path'], read=False)
BODY = dict(MESSAGE, body='Hello\x1b[31m world', recipients='other@example.test', attachments=[dict(name='a.txt', size=12)])


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


def test_folder_depth_retains_nested_folders_without_parsing_their_uris():
    rows=[FOLDER,dict(FOLDER,name='Child',depth=1,path='opaque:child'),dict(FOLDER,name='Sibling',depth=0,path='opaque:sibling')]
    got=folder_tree(rows)
    assert len(got)==2 and got[0]['children'][0]['path']=='opaque:child'


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


def test_canvas_pagination_body_and_runtime_overlap_filtering():
    with patch.object(settings,'existing_addresses',return_value={'reader@example.test'}):
        p=Thunderbird(dict(accounts=['a1','a2']),client_factory=Fixture)
        try:
            account=loaded(p,p.root())[0]
            assert account.data['id']=='a2'
            folder=loaded(p,account)[0]
            first=loaded(p,folder)
            message=first[0]
            assert p.mark(message) is not None
            more=first[-1];assert more.kind=='tb-more'
            p.choose(more);p.wait();folder.kids=None
            page=folder.load()
            assert len([n for n in page if n.kind=='tb-message'])==2
            assert any(n.name=='Narrow search' for n in page)
            children=loaded(p,message)
            body=next(n for n in children if n.kind=='tb-body')
            assert '\x1b' not in body.name
            assert p.chip_rows(next(n for n in children if n.kind=='tb-sender'))==1
            args=next(a for n,a in p.client.calls if n=='getMessage')
            assert args['bodyFormat']=='text' and args['saveAttachments'] is False
            assert p.reference(body)['priority']==0
        finally: p.close()
        assert p.client.closed


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
    n=fc.Node('/fixture','Message',True,source=p,kind='tb-message',data=MESSAGE)
    try:
        rows=loaded(p,n)
        assert any('Message content withheld' in r.name for r in rows)
    finally:p.close()


@pytest.mark.parametrize('version,ok',[('v18.0.0',True),('v24.0.0',True),('v17.0.0',False),('broken',False)])
def test_doctor_checks_runtime_without_reading_a_thunderbird_session(version,ok):
    import subprocess
    from twigs.kit import doctor
    with patch.object(settings,'load',return_value=dict(accounts=['a2'],node='/test/node.exe')), \
         patch.object(doctor.subprocess,'run',return_value=subprocess.CompletedProcess([],0,version,'')):
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
