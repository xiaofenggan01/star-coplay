"use client";

import Navbar from "@/components/Navbar";
import Hero from "@/components/Hero";
import ProductShowcase from "@/components/ProductShowcase";
import CoreCapabilities from "@/components/CoreCapabilities";
import BeforeAfter from "@/components/BeforeAfter";
import HowItWorks from "@/components/HowItWorks";
import FAQ from "@/components/FAQ";
import FinalCTA from "@/components/FinalCTA";
import Footer from "@/components/Footer";
import { JoinModalProvider } from "@/components/JoinModal";

export default function LandingHome() {
  return (
    <JoinModalProvider>
      <main className="min-h-screen">
        <Navbar />
        <Hero />
        <ProductShowcase />
        <CoreCapabilities />
        <BeforeAfter />
        <HowItWorks />
        <FAQ />
        <FinalCTA />
        <Footer />
      </main>
    </JoinModalProvider>
  );
}
