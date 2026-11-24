import { describe, expect, it } from "vitest";

import wikimediaLoader, { commonsThumbWidth } from "./wikimedia-loader";

const THUMB =
  "https://upload.wikimedia.org/wikipedia/commons/thumb/d/dc/Skyscrapers_of_Shinjuku_2009_January_%28revised%29.jpg/1280px-Skyscrapers_of_Shinjuku_2009_January_%28revised%29.jpg";

describe("commonsThumbWidth", () => {
  it("rounds up to a standard Wikimedia width, at most 1280", () => {
    expect(commonsThumbWidth(100)).toBe(330);
    expect(commonsThumbWidth(330)).toBe(330);
    expect(commonsThumbWidth(331)).toBe(500);
    expect(commonsThumbWidth(640)).toBe(960);
    expect(commonsThumbWidth(1280)).toBe(1280);
    expect(commonsThumbWidth(3840)).toBe(1280);
  });
});

describe("wikimediaLoader", () => {
  it("points at the Commons thumbnail of the requested width", () => {
    expect(wikimediaLoader({ src: THUMB, width: 500 })).toBe(
      THUMB.replace("/1280px-", "/500px-"),
    );
    expect(wikimediaLoader({ src: THUMB, width: 750 })).toBe(
      THUMB.replace("/1280px-", "/960px-"),
    );
  });

  it("never asks for more than the stored width", () => {
    expect(wikimediaLoader({ src: THUMB, width: 2048 })).toBe(THUMB);
    const small = THUMB.replace("/1280px-", "/960px-");
    expect(wikimediaLoader({ src: small, width: 1280 })).toBe(small);
  });

  it("keeps the file name intact, including names that contain 'px-'", () => {
    const src =
      "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Top_100px-view.jpg/1280px-Top_100px-view.jpg";
    expect(wikimediaLoader({ src, width: 330 })).toBe(
      "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Top_100px-view.jpg/330px-Top_100px-view.jpg",
    );
  });

  it("uses an original (not a thumbnail) as it is", () => {
    const original = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Small.jpg";
    expect(wikimediaLoader({ src: original, width: 330 })).toBe(original);
  });

  it("rejects other hosts and query strings", () => {
    expect(() => wikimediaLoader({ src: "https://example.com/1280px-x.jpg", width: 500 })).toThrow();
    expect(() => wikimediaLoader({ src: `${THUMB}?utm_source=x`, width: 500 })).toThrow();
    expect(() =>
      wikimediaLoader({ src: "https://upload.wikimedia.org/wikipedia/en/a/ab/X.jpg", width: 500 }),
    ).toThrow();
  });
});
