// Fictional catalog for mocked API tests; production options come from the API.
export const defaultAccountOptions = {
  tiers: [
    { id: "5x", name: "5x", enabled: true },
    { id: "20x", name: "20x", enabled: true },
  ],
  anomaly_categories: [
    {
      id: "at capacity",
      name: "at capacity",
      enabled: true,
      cooldown_hours: null,
    },
    { id: "降智", name: "降智", enabled: true, cooldown_hours: null },
  ],
  cooldown_hours: 24,
};
