import { LegalDoc } from "@/components/legal/LegalDoc";
import { DOCS } from "@/lib/legal";

export const metadata = { title: `${DOCS["subprocessors"].title} · LeadX`, description: DOCS["subprocessors"].summary };

export default function Page() {
  return <LegalDoc slug="subprocessors" />;
}
