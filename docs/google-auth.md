# Google account paths

Twigs currently uses IMAP/SMTP via mbsync/msmtp, with app passwords or NeoMutt's OAuth helper.
There is no gws backend or first-party twigs OAuth client yet.

## Seven-day expiry

For an external OAuth app left in Google's **Testing** publishing state, refresh tokens for
Gmail/Calendar scopes generally expire after seven days. This affects each user who authorizes
that testing app, including its developer. Access-token refresh happens automatically, but an
expired refresh token requires a fresh browser authorization. Moving the app to Production
removes that particular testing limit; authorize again to obtain a new grant. Tokens can still
expire or be revoked for other reasons.
Production is not the same thing as verified: broad public use of sensitive/restricted scopes
requires Google's applicable verification process. Personal-use exceptions exist.

For a personal Gmail account now, an app password is the least setup if the account permits
it and has two-step verification. It is unavailable for some organization/security configurations
and is revoked after a Google password change. For a public application, twigs' own desktop
OAuth client with browser authorization, PKCE, secure local refresh-token storage and refresh
handling is the preferred user experience.

## gws

`gws auth setup` automates Cloud project/API/OAuth setup using `gcloud`.
`gws auth login -s gmail,calendar` filters its scope selector to those services. Its own
OAuth project still has the same Testing/Production rules. Request only the necessary scopes.

This can be an optional advanced-user path. A future twigs integration could call gws's Gmail
and Calendar APIs, leaving gws to manage its own credentials and refresh. Merely running those
auth commands does not connect today's twigs mail backend. For IMAP/SMTP, authorization must
include `https://mail.google.com/`; narrower Gmail API scopes are insufficient. Do not scrape
gws's encrypted credential store or use another application's client identity.

agy's existing AI/Cloud/identity grants do not provide Gmail or Calendar access.

Sources: [Google token expiration](https://developers.google.com/identity/protocols/oauth2#expiration),
[desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app),
[restricted-scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification),
[app passwords](https://support.google.com/accounts/answer/185833),
[Gmail IMAP/SMTP scope](https://developers.google.com/workspace/gmail/imap/xoauth2-protocol),
[gws authentication](https://github.com/googleworkspace/cli#authentication).
