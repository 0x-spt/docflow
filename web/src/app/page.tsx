import { AuditBlock } from "@/components/landing/AuditBlock";
import { Checks } from "@/components/landing/Checks";
import { DigitalCopy } from "@/components/landing/DigitalCopy";
import { Faq } from "@/components/landing/Faq";
import { Hero } from "@/components/landing/Hero";
import { HowItWorks } from "@/components/landing/HowItWorks";
import { Integrations } from "@/components/landing/Integrations";
import { Measured } from "@/components/landing/Measured";
import { Niches } from "@/components/landing/Niches";
import { Problem } from "@/components/landing/Problem";
import { PublicPage } from "@/components/landing/PublicPage";
import { Rollout } from "@/components/landing/Rollout";
import { Rules } from "@/components/landing/Rules";
import { Security } from "@/components/landing/Security";

// Порядок блоков — путь посетителя: боль → куда уходят данные → правила → как работает → проверки → бонус PDF
// → ниши → замер → безопасность → внедрение → вопросы → заявка.
export default function Home() {
  return (
    <PublicPage>
      <Hero />
      <Problem />
      <Integrations />
      <Rules />
      <HowItWorks />
      <Checks />
      <DigitalCopy />
      <Niches />
      <Measured />
      <Security />
      <Rollout />
      <Faq />
      <AuditBlock />
    </PublicPage>
  );
}
