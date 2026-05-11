Bug priority policy (P0-P4)

P0 Outage
Definition:
- Component/platform is non-functional
- Core workflow unavailable for many or all policies/claims
Examples:
- API unavailable
- Major component down (quoting/compliance/reporting)
- Third-party integration failure blocking office operations
- Build broken, halting development
- Agents cannot quote any business
- Renewals not processing
- BriteAuth down; users cannot log in

P1 Critical
Definition:
- Major customer business interruption, with or without workaround
- Also includes aged/escalated P2 with stronger proven impact
- Also includes VIP/at-risk cases that would otherwise be P2
Examples:
- Financial data loss
- Data fully missing in feature
- Business-impacting data error
- Major performance regression
- Critical security/compliance issue
- Renewal invoices not auto-generating (manual workaround exists, issue persists)
- Issues that can cause client business loss (e.g., incorrect/duplicate invoices, quoting malfunction, document generation failures)

P2 High
Definition:
- Noticeable (not severe) business impact with reasonable workaround
- Reasonable workaround = minimal customer/internal effort and impact
- Also includes aged/escalated P3 with stronger proven impact
- Also includes VIP/at-risk cases that would otherwise be P3
Examples:
- Some use cases broken, component still functional overall, workaround exists
- Automated process broken but manual process works
- Some non-business-operation reports cannot be downloaded
- Incorrect/missing non-financial data
- Rollup counts incorrect in report
- Implementation blocker
- Data metric mismatch across pages

P3 Normal
Definition:
- Minimal business impact with workaround available, or
- No business impact without workaround
Examples:
- Graph not updating but downloadable data is available
- Column does not sort
- Minor web element defect while overall feature remains functional

P4 Nice-to-have
Definition:
- Trivial issue or enhancement request
- Very minimal or no business impact
Examples:
- Spelling/grammar fixes
- Error shown when user lacks proper permissions
- Cosmetic UI inconsistency without functional impact
