# Cookie Policy

**Effective Date:** February 14, 2026
**Last Updated:** February 14, 2026
**Entity:** PBIChat
**Contact:** support@pbichat.com
**Website:** https://pbichat.com

---

## 1. Overview

This Cookie Policy explains how PBIChat uses cookies and similar technologies. The short version: **PBIChat does not use cookies for tracking or analytics.** We use browser localStorage for session management instead.

## 2. What PBIChat Uses

### 2.1 localStorage (Not Cookies)

PBIChat uses browser **localStorage** — not cookies — to store:

| Key | Purpose | Data Stored |
|-----|---------|-------------|
| JWT session token | Keeps you logged in | Encrypted authentication token |
| Chat history | Preserves recent conversations | Last 20 chat messages |

localStorage data:
- Is stored only in your browser
- Is not sent to our servers with every request (unlike cookies)
- Is not shared with third parties
- Is cleared when you log out or clear your browser data
- Does not track you across websites

### 2.2 No Tracking Cookies

PBIChat does **not** use:
- Analytics cookies (no Google Analytics, no Mixpanel, no Amplitude)
- Advertising or retargeting cookies
- Third-party tracking pixels
- Fingerprinting or cross-site tracking of any kind

## 3. Third-Party Cookies

### 3.1 Stripe Checkout

When you upgrade to the Pro tier, you are redirected to **Stripe Checkout** (a Stripe-hosted payment page). Stripe may set its own cookies on the `checkout.stripe.com` domain for:
- Fraud prevention
- Payment session management
- Security

These cookies are set by Stripe, not by PBIChat, and are governed by [Stripe's Cookie Policy](https://stripe.com/cookies-policy/legal). PBIChat has no access to or control over Stripe's cookies.

### 3.2 No Other Third-Party Cookies

No other third-party services used by PBIChat (Supabase, OpenRouter) set cookies in your browser through the PBIChat visual or API.

## 4. Power BI Context

The PBIChat visual runs inside Microsoft Power BI (Desktop or Service). Power BI itself may use cookies and local storage for its own purposes — those are governed by [Microsoft's Privacy Statement](https://privacy.microsoft.com/privacystatement) and are unrelated to PBIChat.

## 5. Managing Your Data

Since PBIChat uses localStorage rather than cookies:
- **To clear PBIChat data:** Log out via the visual's Settings panel, or clear your browser's localStorage
- **To clear Stripe cookies:** Use your browser's cookie management settings for the `stripe.com` domain

No cookie consent banner is required for PBIChat because we do not set cookies. The Stripe Checkout page handles its own cookie consent where required.

## 6. Changes to This Policy

We may update this Cookie Policy from time to time. Changes will be reflected in the "Last Updated" date above.

## 7. Contact Us

For questions about this Cookie Policy:

- **Email:** support@pbichat.com
- **Website:** https://pbichat.com
