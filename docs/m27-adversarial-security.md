# M2.7 threat-regression increment (no real model calls)

This adds a targeted Agent-container test matrix for hazards that a
real commerce Assistant must never turn into privileged actions.

1. **Prompt injection from product description.** The model sees an
   attacker-authored description asking it to execute
   `submit_after_sale`. Even if it attempts the call, the model-facing
   tool registry disallows that operation; only confirmation-controlled
   backend logic can perform it. The test asserts no MCP write, preview
   or operation side effect.
2. **Raw provider exception redaction.** A synthetic upstream error
   carrying a fake credential cannot reach persisted Agent run events.
3. **DNS rebinding / mixed answers.** An apparently public HTTPS host
   resolving to a mix of public and loopback IP addresses must be
   refused *before* outbound socket creation. Requests to non-global
   addresses, URLs with embedded credentials, non-HTTPS schemes and
   non-standard ports remain prohibited.

These tests use only local stand-ins and no external API Keys,
deployments, payments or real customers. They extend—not replace—
the existing owner-scope, confirmation-token, SQL/Outbox and
MCP integration tests. They demonstrate that the **host authorization
boundary blocks specific attacks**, not that arbitrary LLM-generated
text is semantically safe. Future red-team runs with a real model must
use a separate, explicit, paid-model budget and independent labeling.

Run on the Agent image:
```bash
python -m pytest services/agent/tests/test_m27_adversarial_security.py -q
```
GitHub `Shop quality` runs Agent tests with `--network none`;
this matrix must execute in both tracing configurations.
