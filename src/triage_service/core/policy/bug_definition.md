Bug vs Story policy

Classification precedence (highest first):
1) Intentional change / new UX flow check
2) New capability or improvement check
3) Bug criteria check

If a higher-precedence rule applies, stop and classify there.

Bug definition:
A ticket is a Bug only when ALL are true and evidenced in the ticket:
- Behavior violates expected functionality in the CURRENT intended product behavior
- Expected behavior is explicitly defined (requirements/UI/validation) OR explicitly required parity with prior behavior
- Issue is reproducible
- Impact includes at least one: incorrect data, system error, broken workflow, or inability to complete intended function
- The behavior is NOT an intentional product/design/code change

Important guardrail:
- “Previously worked” by itself is not enough for Bug.
- In migrations/new UI/new workflow releases, if parity is not explicitly required, classify as Story.

Bug examples:
- A quote submission fails with an error when all required fields are valid
- A report shows incorrect calculated values
- A button does nothing when clicked
- A committed requirement is implemented, but behaves incorrectly
- A previously working workflow now fails unexpectedly (and not due to intentional redesign)

Not a Bug (create Story instead):
- The behavior is confusing or unintuitive
- The user expects different behavior
- The workflow is inefficient
- The feature is missing functionality
- The experience could be improved
- The requirement was missed OR not implemented
- The client requests changes to data format
- The client provides new data requirements or implementation artifacts
- The client makes additional requests beyond existing functionality
- Intentional code/product/UI/workflow change removes or alters existing capability
- Old UI supported behavior that new UI does not, without explicit parity requirement

These should be created as a Story, not a Bug.

Common classification rules:
- "Fewer clicks wanted" -> Story (improvement)
- "Desired workflow not supported" -> Story (new capability)
- "UI is confusing/misleading" -> Story or UX improvement
- "Validation too strict" -> Story unless clearly incorrect per explicit spec
- "Behavior differs from customer expectation" -> Story unless explicit requirement is violated
- "Previously working behavior now broken" -> Bug only if there is evidence it is unintended and violates explicit expected behavior/parity
- "Behavior changed in new UI/release and may be intentional" -> Story by default; escalate to Bug only with explicit requirement evidence

Decision flow:
1) Does the ticket describe a likely intentional behavior change in a new UI/workflow/release?
   - Yes/likely/unclear -> Story (unless explicit requirement/parity violation is cited)
   - No -> Continue
2) Is the request asking for a new capability or improvement?
   - Yes -> Story
   - No -> Continue
3) Is something broken (reproducible malfunction)?
   - No -> Story
   - Yes -> Continue
4) Is there explicit evidence of mismatch with defined expected behavior (or explicitly required parity)?
   - Yes -> Bug
   - No/unclear -> Story
5) Final Bug safety check: could this still be intentional product/design change?
   - Yes/unclear -> Story
   - No -> Bug