from quantx_api.gqlapi.operation_policy import operation_policy
from quantx_api.gqlapi.resolvers.entry_plans import EntryPlanResolver
from quantx_api.gqlapi.schema import schema


def _definition(sdl: str, header: str) -> str:
  return sdl.split(header, 1)[1].split("}", 1)[0]


def test_entry_plan_schema_is_strongly_typed_and_single_account_scoped():
  sdl = schema.as_str()

  create_input = _definition(sdl, "input CreateEntryPlanInput {")
  update_input = _definition(sdl, "input UpdateEntryPlanInput {")
  preview_input = _definition(
    sdl, "input EntryPlanAuthorizationPreviewInput {"
  )
  confirmation_input = _definition(
    sdl, "input EntryPlanAuthorizationConfirmationInput {"
  )

  for definition in (create_input, update_input, preview_input, confirmation_input):
    assert "accountId" not in definition
  assert "targetPolicy: EntryPlanTargetInput!" in create_input
  assert "triggerRules: [EntryPlanRuleInput!]!" in create_input
  assert "JSON" not in create_input
  target_input = _definition(sdl, "input EntryPlanTargetInput {")
  assert "baselineSnapshot" not in target_input
  assert "idempotencyKey: String!" in preview_input
  assert "confirmationToken: String!" in confirmation_input
  assert "snapshotHash" not in confirmation_input


def test_entry_plan_schema_exposes_product_queries_and_commands():
  sdl = schema.as_str()

  for field in (
    "entryPlans(",
    "entryPlan(",
    "entryPlanCapabilities:",
    "entryPlanEvents(",
    "pendingEntryIntents(",
    "entryAutomationStatus:",
    "createEntryPlan(",
    "updateEntryPlan(",
    "setEntryPlanEnabled(",
    "cancelEntryPlan(",
    "evaluateEntryPlanNow(",
    "triggerEntryPlanManualRule(",
    "previewEntryPlanAuthorization(",
    "confirmEntryPlanAuthorization(",
    "previewEntryIntent(",
    "confirmEntryIntent(",
    "rejectEntryIntent(",
    "entryPlanUpdated(",
    "entryIntentUpdated(",
  ):
    assert field in sdl

  plan_output = _definition(sdl, "type EntryPlan {")
  assert "triggerRules: [EntryPlanRule!]!" in plan_output
  assert "pacingPolicy: EntryPlanPacing!" in plan_output
  assert "exitProtection: EntryExitProtection!" in plan_output
  pacing_input = _definition(sdl, "input EntryPlanPacingInput {")
  assert "cashBufferPct: Float!" in pacing_input
  intent_preview = _definition(sdl, "type EntryIntentPreview {")
  assert "confirmationToken: String!" in intent_preview
  mutation = _definition(sdl, "type Mutation {")
  assert (
    "confirmEntryIntent(planId: ID!, intentId: ID!, confirmationToken: String!)"
    in mutation
  )


def test_entry_plan_capabilities_are_the_rule_editor_source_of_truth():
  capabilities = EntryPlanResolver.capabilities
  # The resolver is async only to match GraphQL; the source metadata is pure.
  import asyncio

  projected = asyncio.run(capabilities())
  by_type = {item.rule_type: item for item in projected.rule_types}
  trend = by_type["TREND_PULLBACK_CONFIRMATION"]
  assert {field.key for field in trend.fields} >= {
    "fast_ema_period",
    "slow_ema_period",
    "pullback_pct",
    "rebound_pct",
  }
  assert all(field.help_text for field in trend.fields)
  assert {preset.preset_id for preset in trend.presets} == {
    "CONSERVATIVE",
    "BALANCED",
    "ACTIVE",
  }
  assert all(preset.parameters for preset in trend.presets)
  ladder = by_type["PRICE_LADDER"]
  assert ladder.fields[0].type == "PRICE_LADDER"


def test_entry_plan_sensitive_mutations_have_explicit_approval_policy():
  assert operation_policy(
    "Mutation", "confirmEntryPlanAuthorization"
  ).required_permissions == ("strategy:control", "trade:approve")
  assert operation_policy(
    "Mutation", "confirmEntryIntent"
  ).required_permissions == ("strategy:control", "trade:approve")
  assert operation_policy(
    "Mutation", "previewEntryIntent"
  ).required_permissions == ("strategy:control", "trade:approve")
  assert operation_policy(
    "Mutation", "triggerEntryPlanManualRule"
  ).required_permissions == ("strategy:control", "trade:approve")
  assert operation_policy(
    "Subscription", "entryPlanUpdated"
  ).required_permissions == ("strategy:read",)
  assert operation_policy(
    "Subscription", "entryIntentUpdated"
  ).required_permissions == ("strategy:read",)
