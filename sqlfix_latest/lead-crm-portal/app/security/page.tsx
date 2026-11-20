import { LegalDoc } from "@/components/legal/LegalDoc";
import { DOCS } from "@/lib/legal";

export const metadata = { title: `${DOCS["security"].title} · LeadX`, description: DOCS["security"].summary };

export default function Page() {
  return <LegalDoc slug="security" />;
}
