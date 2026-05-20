Bug vs Story policy

Bug definition:
A ticket is a Bug only when all are true:
- Behavior violates expected functionality
- Expected behavior was previously working or clearly defined (requirements/UI/validation)
- Issue is reproducible
- Impact includes at least one: incorrect data, system error, broken workflow, or inability to complete intended function

Bug examples:
- A quote submission fails with an error when all required fields are valid
- A report shows incorrect calculated values
- A button does nothing when clicked
- A workflow that previously worked is now broken
- A requirement WAS implemented, but is working incorrectly.

Not a Bug (create Story instead):
- The behavior is confusing or unintuitive
- The user expects different behavior
- The workflow is inefficient
- The feature is missing functionality
- The experience could be improved
- The requirement was missed OR not implemented.
- The client requests changes to data format
- The client provides new data requirements or implementation artifacts.
- The client makes additional requests beyond existing functionality.

These should be created as a Story, not a Bug.

Common classification rules:
- "Fewer clicks wanted" -> Story (improvement)
- "Desired workflow not supported" -> Story (new capability)
- "UI is confusing/misleading" -> Story or UX improvement
- "Validation too strict" -> Story unless clearly incorrect per spec
- "Behavior differs from customer expectation" -> validate with Product (expectation alone is not defect)
- "Previously working behavior now broken" -> Bug

Decision flow:
1) Is the request asking for a new capability or improvement?
   - Yes -> Story
   - No -> Continue
2) Is something broken?
   - No -> Story
   - Yes -> continue
3) Is there a clear mismatch with defined or previously working behavior?
   - Yes -> Bug
   - No/unclear -> likely a Story.