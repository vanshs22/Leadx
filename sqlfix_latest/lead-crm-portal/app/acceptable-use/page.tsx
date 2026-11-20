import { LegalDoc } from "@/components/legal/LegalDoc";
import { DOCS } from "@/lib/legal";

export const metadata = { title: `${DOCS["acceptable-use"].title} · LeadX`, description: DOCS["acceptable-use"].summary };

export default function Page() {
  return <LegalDoc slug="acceptable-use" />;
}
