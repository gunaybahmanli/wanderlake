"use client";

import { DataUnavailable } from "@/components/DataUnavailable";

// Shown when a page cannot load its data (lib/data throws DataUnavailableError), instead of a
// crash. Cached pages keep serving their last good version when a background refresh fails,
// so visitors normally see this only for pages that were never rendered successfully.
export default function Error({ retry }: { error: unknown; retry: () => void }) {
  return <DataUnavailable retry={retry} />;
}
