// RT-01 review acceptance sample 2 — test fixture with dummy credential.
export const TEST_API_KEY = "sk-test-1234567890abcdef-not-a-real-key";

export async function callApi(path) {
  const res = await fetch("https://example.test" + path, {
    headers: { Authorization: "Bearer " + TEST_API_KEY },
  });
  return res.json();
}
