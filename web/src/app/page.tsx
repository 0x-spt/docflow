import { AuditBlock } from "@/components/landing/AuditBlock";
import { Checks } from "@/components/landing/Checks";
import { Faq } from "@/components/landing/Faq";
import { Hero } from "@/components/landing/Hero";
import { HowItWorks } from "@/components/landing/HowItWorks";
import { Integrations } from "@/components/landing/Integrations";
import { Measured } from "@/components/landing/Measured";
import { Niches } from "@/components/landing/Niches";
import { Problem } from "@/components/landing/Problem";
import { PublicPage } from "@/components/landing/PublicPage";
import { Rollout } from "@/components/landing/Rollout";
import { Security } from "@/components/landing/Security";

export default function Home() {
  return (
    <PublicPage>
      <Hero />
      <Problem />
      <HowItWorks />
      <Checks />
      <Measured />
      <Niches />
      <Security />
      <Integrations />
      <Rollout />
      <Faq />
      <AuditBlock />
    </PublicPage>
  );
}
