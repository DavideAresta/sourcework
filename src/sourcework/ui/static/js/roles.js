// The four model roles, named for the reader rather than for the code.
//
// This file exists because the same four strings were written twice, in the run
// form and on the settings page, with two different wordings — so the same
// control explained itself differently depending on which page you were on. The
// *set* of roles comes from the server (`/api/backends` → `roles`, derived from
// the settings fields that can actually configure one); what follows is only how
// each is worded.

export const ROLE_LABEL = {
  default: 'Everyday work',
  reasoning: 'Hard thinking',
  vision: 'Reading images',
  critic: 'Adversarial review',
};

export const ROLE_HELP = {
  default: 'Ingestion, drafting, publishing',
  reasoning: 'The analyst and the writer — the calls that decide whether the PRD is any good',
  vision: 'Screenshots, wireframes, diagrams',
  critic: 'Best from another family — a critic that shares the writer\'s training shares its blind spots',
};

// A role the server names but this file has no wording for still gets a control,
// labelled with its own id. Silently dropping it would hide a configurable model
// behind a UI that never mentioned it.
export function roleLabel(role) {
  return ROLE_LABEL[role] ?? role;
}

export function roleHelp(role) {
  return ROLE_HELP[role] ?? '';
}
