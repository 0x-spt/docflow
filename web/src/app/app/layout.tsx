import type { ReactNode } from "react";

import { AppShell } from "@/components/app/AppShell";
import { requireMe } from "@/lib/session";

export const metadata = { title: "Кабинет — DocFlow", robots: { index: false } };

export default async function CabinetLayout({ children }: { children: ReactNode }) {
  const me = await requireMe();
  return (
    <AppShell tenantName={me.tenant.name} extractor={me.tenant.extractor}>
      {children}
    </AppShell>
  );
}
