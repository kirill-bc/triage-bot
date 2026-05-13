Bug vs Story policy

Bug definition:
A ticket is a Bug only when all are true:
- Behavior violates expected functionality
- Expected behavior was previously working or clearly defined (requirements/UI/validation)
- Issue is reproducible
- Impact includes at least one: incorrect data, system error, broken workflow, or inability to complete intended function

Bug examples:
- Quote submission fails with valid required fields
- Report shows incorrect calculated values
- Button click does nothing
- Previously working workflow is now broken (regression)

Not a Bug (create Story instead):
- System works as designed, but behavior is confusing or unintuitive
- User expected different behavior without defined mismatch
- Workflow is inefficient
- Missing functionality/new capability request
- Experience improvement request

Common classification rules:
- "Fewer clicks wanted" -> Story (improvement)
- "Desired workflow not supported" -> Story (new capability)
- "UI is confusing/misleading" -> Story or UX improvement
- "Validation too strict" -> Story unless clearly incorrect per spec
- "Behavior differs from customer expectation" -> validate with Product (expectation alone is not defect)
- "Previously working behavior now broken" -> Bug

Decision flow:
1) Is something broken?
   - No -> Story
   - Yes -> continue
2) Is there a clear mismatch with defined or previously working behavior?
   - Yes -> Bug
   - No/unclear -> check with Product
3) Is this asking for new capability or improvement?
   - Yes -> Story
