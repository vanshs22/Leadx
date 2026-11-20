import { LegalDoc } from "@/components/legal/LegalDoc";
import { DOCS } from "@/lib/legal";

export const metadata = { title: `${DOCS["terms"].title} · LeadX`, description: DOCS["terms"].summary };

export default function Page() {
  return <LegalDoc slug="terms" />;
}
