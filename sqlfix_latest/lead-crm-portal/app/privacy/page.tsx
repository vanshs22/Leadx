import { LegalDoc } from "@/components/legal/LegalDoc";
import { DOCS } from "@/lib/legal";

export const metadata = { title: `${DOCS["privacy"].title} · LeadX`, description: DOCS["privacy"].summary };

export default function Page() {
  return <LegalDoc slug="privacy" />;
}
