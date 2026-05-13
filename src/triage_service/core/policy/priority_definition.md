Bug priority policy (P0-P4)

P0 Outage
Definition:
Component or platform is non-functional and not working as expected.
Examples:

- API is unavailable. 
- Major component is unavailable or non-functional (i.e. agent quoting, compliance issue, reporting issue) for multiple or all policies/claims.  Solution can include a new feature. 
- 3rd party vendor integration(s) failing, thus blocking daily office operations. 
- Build is broken halting all development. 
- Agents cannot quote any business 
- Renewals are not processing 
- BriteAuth is non-functional and users cannot log-in.

P1 Critical
Definition:
- Major customer business impact - whether a workaround is available or not, any defect causing a major business interruption for a client 
OR
- Bugs originally set to P2 that have been open for an extended period of time and the client has asked for updates and/or provided additional examples of the issue that indicate a more severe business impact than the original report. 
- Bugs that would typically be considered P2s for VIP clients/at-risk clients.

Examples:
- Would be a P2 but customer is a VIP or at-risk. 
- Financial data loss error. 
- Data fully missing in feature. 
- Business impacting data error. 
- Major performance regression. 
- Critical security/compliance related issue. 
- Renewal Invoices are not automatically generating in Attachments, but can be manually generated. Issue has persisted for several weeks. 
- Any issue that negatively impacts agents or insureds and could cause clients to lose business - for example, incorrect or duplicate invoices sent to insureds, quoting system isn't working correctly, documents not generating correctly for agents.

P2 High
Definition:
- Feature with non-severe but noticeable business impact is not working but a reasonable* workaround is available. 
- A reasonable workaround can be implemented with minimal impact to customer business/resources and/or internal resources. These workarounds do not require much time or effort. 
OR
- Bugs originally set to P3 that have been open for an extended period of time and the client has asked for updates and/or provided additional examples of the issue that indicate a more severe business impact than the original report. 

Examples:
- Would be a P3 but customer is a VIP or at-risk. 
- Bug that affects some use cases but doesn’t make a component nonfunctional, with a workaround for the impacted use cases. 
- Automated process broken though manual process functions 
- Unable to download some non-business operations reports 
- Incorrect/Missing non-financial data 
- Rollup count does not add up in report 
- Implementation Blocker 
- Data metric mismatch in different pages.

P3 Normal
Definition:
- Feature with minimal business impact is not working and a workaround is available. 
OR 
- Feature with no business impact is not working and a workaround is not available.

Examples:
- Graph not updating though user can download data. 
- Column does not sort. 
- Web element not working as designed but overall feature still fully functional.

P4 Nice-to-have
Definition:
- Trivial issue or feature request that is desired but not necessary. The lack of the desired functionality has very minimal/no business impact.
Examples:
- Fix spelling/grammar error. 
- Button throws error when user does not have proper permissions. 
- Inconsistent color/design across pages in the UI that does not impact functionality.
