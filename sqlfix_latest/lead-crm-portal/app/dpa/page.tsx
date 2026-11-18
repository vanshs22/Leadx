import { LegalDoc } from "@/components/legal/LegalDoc";
import { DOCS } from "@/lib/legal";

export const metadata = { title: `${DOCS["dpa"].title} · LeadX`, description: DOCS["dpa"].summary };

export default function Page() {
  return <LegalDoc slug="dpa" />;
}
