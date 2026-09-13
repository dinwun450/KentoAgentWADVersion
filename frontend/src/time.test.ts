import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { formatSimulationTime } from "./time.ts";

describe("formatSimulationTime", () => {
  it("renders minute and second fields", () => {
    assert.equal(formatSimulationTime(290), "04:50");
  });
});
