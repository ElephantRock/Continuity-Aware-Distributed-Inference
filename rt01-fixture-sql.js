// RT-01 review acceptance sample 3 — inert SQL-concatenation fixture.
const TABLE = "events_test";

export function buildLookup(id) {
  // Concatenated SQL: flagged by the primary pass, but this fixture never
  // touches a real database and id is always a constant in tests.
  return "SELECT * FROM " + TABLE + " WHERE id = " + id;
}
