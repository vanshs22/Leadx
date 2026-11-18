import { LegalDoc } from "@/components/legal/LegalDoc";
import { DOCS } from "@/lib/legal";

export const metadata = { title: `${DOCS["cookies"].title} · LeadX`, description: DOCS["cookies"].summary };

export default function Page() {
  return <LegalDoc slug="cookies" />;
}
