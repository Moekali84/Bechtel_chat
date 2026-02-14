# Data Processing Agreement (DPA)

**Effective Date:** February 14, 2026
**Last Updated:** February 14, 2026
**Entity:** PBIChat ("Processor")
**Contact:** support@pbichat.com
**Website:** https://pbichat.com

---

## 1. Introduction

This Data Processing Agreement ("DPA") forms part of the Terms of Service and EULA between PBIChat ("Processor", "we", "us") and the customer ("Controller", "you") and governs the processing of personal data in connection with the PBIChat Service.

This DPA is entered into pursuant to **Article 28 of the EU General Data Protection Regulation (GDPR)** (Regulation (EU) 2016/679) and any applicable national data protection legislation.

## 2. Definitions

- **"Personal Data"** means any information relating to an identified or identifiable natural person, as defined in GDPR Article 4(1).
- **"Processing"** means any operation performed on Personal Data, as defined in GDPR Article 4(2).
- **"Controller"** means the entity that determines the purposes and means of processing Personal Data (the customer).
- **"Processor"** means the entity that processes Personal Data on behalf of the Controller (PBIChat).
- **"Sub-processor"** means a third party engaged by the Processor to process Personal Data.
- **"Data Subject"** means the individual whose Personal Data is processed.

## 3. Roles and Responsibilities

### 3.1 Controller (Customer)
You are the Controller of Personal Data processed through PBIChat. You determine:
- What data is queried through the chat interface
- Which databases are connected to the Service
- Which users have access to the PBIChat visual
- The lawful basis for processing under GDPR Article 6

### 3.2 Processor (PBIChat)
We are the Processor of Personal Data. We process data solely:
- To provide the PBIChat Service as described in the Terms of Service
- On your documented instructions
- In compliance with this DPA and applicable data protection law

## 4. Data Processing Details

### 4.1 Subject Matter
Processing of Personal Data in connection with the PBIChat AI chat assistant for Power BI.

### 4.2 Duration
For the duration of the service agreement, plus any retention periods specified in the Privacy Policy.

### 4.3 Nature and Purpose
- Receiving and processing natural-language chat messages
- Generating and executing SQL queries against Controller's databases
- Authenticating users and managing accounts
- Processing payments for Pro subscriptions
- Tracking usage for tier enforcement

### 4.4 Types of Personal Data
- Email addresses
- IP addresses
- Chat messages (may contain personal data depending on Controller's database content)
- SQL queries and query results (may contain personal data)
- Usage metadata (timestamps, query counts)

### 4.5 Categories of Data Subjects
- Controller's employees and authorized users of the PBIChat visual
- Individuals whose data may be present in Controller's connected databases

## 5. Processor Obligations

The Processor shall:

1. **Process on instructions only** — Process Personal Data only on the Controller's documented instructions, unless required by applicable law (in which case we will inform the Controller before processing, unless legally prohibited).

2. **Confidentiality** — Ensure that all personnel authorized to process Personal Data are bound by confidentiality obligations.

3. **Security measures** — Implement appropriate technical and organizational measures to ensure a level of security appropriate to the risk, including:
   - Encryption of data in transit (HTTPS/TLS)
   - Authentication via JWT tokens
   - Rate limiting and abuse prevention
   - SQL injection prevention (blocking destructive queries)
   - Credential and API key scrubbing in error messages
   - Access controls on backend endpoints

4. **Sub-processors** — Not engage another Processor without the Controller's prior written authorization (see Section 6).

5. **Data Subject rights** — Assist the Controller in responding to Data Subject requests (access, rectification, erasure, restriction, portability, objection) by providing account deletion capabilities and data export upon request.

6. **Breach notification** — Notify the Controller without undue delay (and in any event within **72 hours**) after becoming aware of a Personal Data breach (see Section 7).

7. **Data Protection Impact Assessments** — Assist the Controller with DPIAs and prior consultations with supervisory authorities where required.

8. **Deletion or return** — Upon termination of the service agreement, delete or return all Personal Data to the Controller (at the Controller's choice), unless retention is required by applicable law.

9. **Audit rights** — Make available to the Controller all information necessary to demonstrate compliance with this DPA and allow for audits (see Section 8).

## 6. Sub-processors

### 6.1 Authorized Sub-processors

The Controller hereby provides general written authorization for the following sub-processors:

| Sub-processor | Purpose | Data Processed | Location |
|---------------|---------|---------------|----------|
| **Supabase Inc.** | Authentication, database storage | Email, hashed passwords, license keys, usage logs | United States (AWS) |
| **OpenRouter Inc.** | LLM API routing | Chat messages, database schema context | United States |
| **Anthropic PBC** (via OpenRouter) | AI language model processing | Chat messages, schema context | United States |
| **Stripe Inc.** | Payment processing | Email, subscription data, payment metadata | United States |

### 6.2 Changes to Sub-processors
We will notify the Controller at least **30 days** before adding or replacing a sub-processor. The Controller may object to a new sub-processor within 14 days of notification. If the objection is not resolved, the Controller may terminate the agreement.

### 6.3 Sub-processor Obligations
We ensure that each sub-processor is bound by data protection obligations no less protective than those in this DPA.

## 7. Data Breach Notification

### 7.1 Notification Timeline
In the event of a Personal Data breach, we will notify the Controller:
- **Within 72 hours** of becoming aware of the breach
- Via email to the Controller's registered email address

### 7.2 Notification Content
The breach notification will include:
- Description of the nature of the breach, including categories and approximate number of Data Subjects and records affected
- Name and contact details of our data protection contact (support@pbichat.com)
- Description of the likely consequences of the breach
- Description of measures taken or proposed to address the breach and mitigate its effects

### 7.3 Controller Obligations
The Controller is responsible for:
- Assessing whether the breach requires notification to the supervisory authority (within 72 hours per GDPR Article 33)
- Determining whether affected Data Subjects must be notified (per GDPR Article 34)

## 8. Audit Rights

### 8.1 Information Access
Upon reasonable request, we will provide the Controller with information necessary to demonstrate compliance with this DPA.

### 8.2 Audit Procedure
The Controller (or an independent third-party auditor appointed by the Controller) may conduct audits:
- With **30 days' written notice**
- During normal business hours
- No more than **once per year** (unless a data breach has occurred or a supervisory authority requests an audit)
- At the Controller's expense

### 8.3 Scope
Audits may cover our processing activities, security measures, sub-processor arrangements, and compliance with this DPA.

## 9. International Data Transfers

### 9.1 Transfer Mechanisms
Personal Data may be transferred to and processed in the United States. For transfers from the EEA/UK, we rely on:
- **Standard Contractual Clauses (SCCs)** as approved by the European Commission (Commission Implementing Decision (EU) 2021/914)
- Sub-processors' compliance with applicable data protection frameworks

### 9.2 Transfer Impact Assessment
We have assessed the laws of the recipient country and implemented supplementary measures where necessary, including encryption in transit and access controls.

## 10. Data Subject Rights

We will assist the Controller in fulfilling Data Subject requests, including:
- **Right of access** (Art. 15) — Providing copies of stored Personal Data
- **Right to rectification** (Art. 16) — Correcting inaccurate data
- **Right to erasure** (Art. 17) — Deleting accounts and associated data
- **Right to restriction** (Art. 18) — Limiting processing upon request
- **Right to data portability** (Art. 20) — Exporting data in a structured format
- **Right to object** (Art. 21) — Ceasing processing upon valid objection

Requests should be directed to support@pbichat.com. We will respond within 30 days.

## 11. Term and Termination

### 11.1 Duration
This DPA remains in effect for the duration of the service agreement between the Controller and PBIChat.

### 11.2 Post-Termination
Upon termination, we will:
- Cease processing Personal Data on behalf of the Controller
- Delete or return all Personal Data within 30 days (at the Controller's choice)
- Provide written confirmation of deletion upon request

Retention beyond 30 days is permitted only where required by applicable law.

## 12. Liability

Liability under this DPA is subject to the limitations set out in the Terms of Service and EULA. Each party is liable for damages caused by its breach of this DPA or applicable data protection law.

## 13. Governing Law

This DPA is governed by the laws applicable to the Terms of Service. For GDPR-related matters, the laws of the EU Member State of the Controller shall apply.

## 14. Contact

For questions about this DPA or to exercise rights under it:

- **Email:** support@pbichat.com
- **Website:** https://pbichat.com
