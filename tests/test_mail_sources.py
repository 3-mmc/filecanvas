"""Several email sources share one list without losing their own routing or updates."""
from unittest.mock import patch
from twigs import core as fc
from twigs.kit import hub, together
from twigs.apps.thunderbird.canvas import Thunderbird


class Source(fc.Provider):
    def __init__(self, path, account):self.path,self.account=path,account
    def root(self):return fc.Node(self.path,'mail',True,source=self,kind='accounts')
    def children(self,node):
        if node.kind=='accounts':return [fc.Node(self.path+'/account',self.account,True,node,'account')]
        return [fc.Node(node.path+'/body','Mail content',False,node)] if node.kind=='account' else []
    def count(self,node):return ('3','filter',True)
    def bar(self,node,plain):return [],[(self.account,'seg_fg','seg_bg',False)]
    def choose(self,node):return None


def make():
    a,b=Source('/manual','Manual mail'),Source('/thunderbird','Thunderbird mail')
    combined=hub.combine([('Emails',a),('Messages',fc.Tree({'chat':1})),('Emails',b)])
    p=together.Together(combined)
    return a,b,p


def test_one_email_section_contains_both_accounts_with_original_owners_and_paths():
    a,b,p=make()
    sections=p.root().load()
    assert [s.name for s in sections]==['Emails','Messages']
    accounts=sections[0].load()
    assert [n.name for n in accounts]==['Manual mail','Thunderbird mail']
    assert [n.source for n in accounts]==[a,b]
    assert [n.path for n in accounts]==['/manual/account','/thunderbird/account']
    assert all(n.parent is sections[0] for n in accounts)
    for n in accounts:
        assert p.count(n)==('3','filter',True)
        assert p.bar(n,True)[1][0][0]==n.name
        assert p.choose(n) is None
        assert n.load()[0].source is n.source


def test_async_source_root_changes_invalidate_the_visible_email_list():
    a,b,p=make()
    b.invalidate('/thunderbird',deep=True)
    assert ('§Emails','deep') in p.stale()
    b.invalidate('/thunderbird/account',deep=True)
    assert ('/thunderbird/account','deep') in p.stale()
    b.signal(('pulse','/thunderbird/account'))
    assert p.signals()==[('pulse','/thunderbird/account')]


def test_merged_accounts_render_as_siblings_and_refresh_after_source_load():
    a,b,p=make()
    c=fc.Canvas('',fc.options(provider=p,backdrop='off',ease=1,glyphs='plain'))
    fc.apply(c,'right',c.opts)
    lines,_=c.frame(120,30,animate=False)
    assert any('Manual mail' in l for l in lines)
    assert any('Thunderbird mail' in l for l in lines)
    b.account='Updated mailbox';b.invalidate('/thunderbird',deep=True);c.refresh()
    lines,_=c.frame(120,30,animate=False)
    assert any('Updated mailbox' in l for l in lines)


def test_thunderbird_origin_is_in_bar_rather_than_the_account_name():
    p=Thunderbird(dict(accounts=[]))
    try:
        root=p.root()
        account=fc.Node('/thunderbird/a','reader@example.test',True,root,'tb-account',data={'id':'a'})
        assert p.label(account)=='reader@example.test'
        assert p.bar(account,True)[1][0][0]=='via Thunderbird'
    finally:p.close()


def test_hub_closes_each_original_provider_after_a_merged_canvas():
    a,b=Source('/a','a'),Source('/b','b')
    a.close=lambda:None;b.close=lambda:None
    with patch.object(a,'close') as first,patch.object(b,'close') as second,patch.object(fc,'pick'):
        hub.open([('Emails',a),('Emails',b)],intro=False)
    first.assert_called_once();second.assert_called_once()
