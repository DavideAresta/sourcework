=== EARS SYNTAX IS ON ===

Write every requirement statement in one of the EARS shapes:

- Ubiquitous:    "The system shall <response>."
- Event-driven:  "When <trigger>, the system shall <response>."
- State-driven:  "While <state>, the system shall <response>."
- Unwanted:      "If <undesired condition>, then the system shall <response>."
- Optional:      "Where <feature is present>, the system shall <response>."

The critic checks the shapes deterministically, and a statement that takes
none of them comes back as a finding.
