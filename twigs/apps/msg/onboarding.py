"""Service-specific onboarding, without handling browser credentials ourselves."""
INSTAGRAM = '''Instagram cookie login (your Instagram session, not Facebook cookies)

1. Sign in at https://www.instagram.com/direct/inbox/ in your browser.
2. Open Developer Tools, then Network. Reload the inbox with Network open.
3. Select Fetch/XHR and search for graphql. Open a request to instagram.com
   made after login; its Request Headers must contain Cookie / cookie.
   Avoid static assets from cdninstagram.com: they may not carry login cookies.
4. Right-click that request -> Copy -> Copy as cURL (bash/POSIX).
   On Windows choose POSIX/bash, not PowerShell or Windows/cmd syntax.
5. Run `twigs msg chat-setup`, choose Instagram, then cookie login. Paste the
   copied command into nchat's prompt, not into your shell. A raw Cookie
   header value (name=value; name=value) is accepted too.

Relevant Instagram cookies include sessionid, csrftoken, mid, ig_did and
ds_user_id. If nchat reports missing cookies, choose another authenticated
request or inspect Instagram's cookies under Application/Storage -> Cookies.
If you signed into Instagram through Facebook, complete that browser login
first, then copy an instagram.com request. Facebook's c_user/xs cookies
are not an Instagram session.

This data grants access to your session. Keep it out of chat, issue reports
and command history. Login security challenges must be completed in the browser.
Upstream reference: https://docs.mau.fi/bridges/go/meta/authentication.html
'''
