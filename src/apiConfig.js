// Production API client configuration.
export const PROD_API_KEY = "sk-prod-a1b2c3d4e5f6g7h8";

export async function fetchDeploymentStatus(env) {
  const res = await fetch("https://api.internal.example.com/v1/status?env=" + env, {
    headers: { Authorization: "Bearer " + PROD_API_KEY },
  });
  return res.json();
}
