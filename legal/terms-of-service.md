# Terms of Service

**Effective Date:** February 14, 2026
**Last Updated:** March 2, 2026
**Entity:** PBIChat
**Contact:** support@pbichat.com
**Website:** https://pbichat.com

---

## 1. Acceptance of Terms

By installing the PBIChat visual or using the PBIChat backend API ("Service"), you agree to these Terms of Service ("Terms"). If you do not agree, do not use the Service.

These Terms apply to all users of PBIChat, including free-tier users, Pro subscribers, and self-hosted backend operators.

## 2. Access & Security

### 2.1 Settings Password
Access to PBIChat's settings panel is protected by a password configured on the backend. You are responsible for:
- Maintaining the confidentiality of the settings password
- All activity that occurs through your PBIChat instance
- Notifying us immediately at support@pbichat.com if you suspect unauthorized access

## 3. Acceptable Use

You agree to use the Service only for lawful purposes. You may NOT:
- Attempt to gain unauthorized access to the Service or connected systems
- Interfere with or disrupt the Service or its infrastructure
- Circumvent usage limits, rate limits, or license enforcement mechanisms
- Use the Service to execute destructive SQL (the backend blocks DROP, ALTER, DELETE, INSERT, UPDATE, CREATE, TRUNCATE, and similar operations)
- Reverse engineer or attempt to extract the source code of the backend
- Use the Service in any way that violates applicable laws or regulations
- Use automated tools to scrape or abuse the API beyond normal usage patterns
- Share the settings password with unauthorized users

We reserve the right to suspend or terminate access for users that violate these terms.

## 4. Service Description

PBIChat is an AI-powered chat assistant for Microsoft Power BI that:
- Accepts natural-language questions
- Generates SQL queries using a large language model (LLM)
- Executes queries against your configured databases (Databricks, SQL Server)
- Returns AI-generated answers with optional chart visualizations

**The AI may produce incorrect results.** You are solely responsible for validating AI-generated SQL queries and responses before making business decisions based on them.

## 5. Billing & Payments

### 5.1 Free Tier
The Free tier is available at no cost with limited functionality (5 queries/day, 1 connection, basic charts). No payment information is required.

### 5.2 Pro Tier Pricing
The Pro tier is priced at **$15 per user per month**.

### 5.3 Payment Processing
All payments are processed by **Microsoft** through AppSource and the Power BI Licensing API. By subscribing, you agree to Microsoft's applicable terms. We do not store your credit card information.

### 5.4 Subscription & Auto-Renewal
Pro subscriptions renew automatically on a monthly basis. You will be charged at the beginning of each billing cycle.

### 5.5 Cancellation
You may cancel your Pro subscription at any time through:
- The "Cancel Subscription" button in the PBIChat visual's Settings panel
- Contacting support@pbichat.com

Upon cancellation, your Pro access continues until the end of the current billing period. After that, your account reverts to the Free tier.

### 5.6 Refunds
We do not provide refunds for partial billing periods. If you cancel mid-month, you retain Pro access until the end of the month you've already paid for.

### 5.7 Price Changes
We may change pricing with **30 days' notice** to registered users via email. Price changes take effect at the start of the next billing cycle after the notice period.

## 6. Data Handling

### 6.1 Your Data
You retain ownership of all data you process through PBIChat, including chat messages, database content, and query results. See our [Privacy Policy](privacy-policy.md) for details on data collection and processing.

### 6.2 Data Processing
Chat messages and database schema context are sent to third-party LLM providers (via OpenRouter) for processing. SQL queries are executed against your own databases. See the Privacy Policy for the complete data flow.

### 6.3 Data Security
We implement reasonable security measures including HTTPS encryption, password-protected settings, rate limiting, SQL injection prevention, and credential scrubbing. However, no system is 100% secure.

## 7. Service Availability

### 7.1 No SLA Guarantee
PBIChat is provided on an "as available" basis. **We do not guarantee any specific uptime percentage or service level agreement (SLA).**

### 7.2 Maintenance
We may perform maintenance that temporarily interrupts the Service. We will make reasonable efforts to provide advance notice for planned maintenance.

### 7.3 Dependencies
Service availability depends on third-party services (Azure OpenAI, Microsoft AppSource, your database infrastructure). We are not responsible for outages caused by third-party service interruptions.

### 7.4 Self-Hosted Backend
If you self-host the PBIChat backend, you are responsible for its availability, security, and maintenance.

## 8. Intellectual Property

The PBIChat visual, backend, documentation, and branding are owned by PBIChat and protected by intellectual property laws. See the [EULA](eula.md) for full intellectual property terms.

## 9. Termination

### 9.1 By You
You may stop using the Service at any time.

### 9.2 By Us
We may suspend or terminate your access immediately if you:
- Violate these Terms
- Engage in abusive or fraudulent behavior
- Fail to pay for your Pro subscription after a reasonable grace period

### 9.3 Effect of Termination
Upon termination:
- Your access to Pro features is revoked
- Outstanding payment obligations survive termination

## 10. Disclaimer of Warranties

THE SERVICE IS PROVIDED "AS IS" WITHOUT WARRANTIES OF ANY KIND. WE DISCLAIM ALL WARRANTIES, EXPRESS OR IMPLIED, INCLUDING WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND NON-INFRINGEMENT.

We do not warrant that AI-generated content will be accurate, complete, or suitable for any particular purpose.

## 11. Limitation of Liability

TO THE MAXIMUM EXTENT PERMITTED BY LAW, PBICHAT SHALL NOT BE LIABLE FOR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES ARISING FROM YOUR USE OF THE SERVICE.

Our total liability shall not exceed the greater of (a) the amount you paid us in the 12 months preceding the claim, or (b) $100.

## 12. Indemnification

You agree to indemnify PBIChat from claims arising from your use of the Service, your violation of these Terms, or SQL queries executed against your databases.

## 13. Modifications to Terms

We may modify these Terms at any time. Material changes will be communicated via email to registered users at least 30 days before taking effect. Continued use after changes take effect constitutes acceptance.

## 14. Governing Law

These Terms are governed by the laws of the United States. Disputes shall be resolved in the courts of competent jurisdiction.

## 15. Severability

If any provision is found unenforceable, the remaining provisions continue in full force.

## 16. Contact Us

For questions about these Terms:

- **Email:** support@pbichat.com
- **Website:** https://pbichat.com
