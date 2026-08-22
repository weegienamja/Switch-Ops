"use client";
import { motion, useReducedMotion } from "motion/react";

/**
 * Subtle animated radial-glow background + network grid texture.
 * No purple SaaS gradient. Dark navy/charcoal only.
 */
export default function AnimatedBackground() {
  const reduce = useReducedMotion();
  return (
    <div aria-hidden className="bg-root">
      <motion.div
        className="bg-glow"
        initial={{ opacity: 0.6 }}
        animate={
          reduce
            ? { opacity: 0.55 }
            : { opacity: [0.45, 0.6, 0.45], scale: [1, 1.04, 1] }
        }
        transition={{ duration: 12, repeat: Infinity, ease: "easeInOut" }}
      />
      <div className="bg-grid" />
      <style jsx>{`
        .bg-root {
          position: fixed;
          inset: 0;
          z-index: -1;
          overflow: hidden;
          background: #070b14;
        }
        .bg-glow {
          position: absolute;
          top: -20%;
          left: 50%;
          width: 80vw;
          height: 80vw;
          transform: translateX(-50%);
          background: radial-gradient(
            circle at center,
            rgba(34, 197, 94, 0.12),
            rgba(34, 211, 238, 0.06) 35%,
            transparent 65%
          );
          filter: blur(40px);
          pointer-events: none;
        }
        .bg-grid {
          position: absolute;
          inset: 0;
          background-image:
            linear-gradient(rgba(148, 163, 184, 0.05) 1px, transparent 1px),
            linear-gradient(90deg, rgba(148, 163, 184, 0.05) 1px, transparent 1px);
          background-size: 48px 48px;
          mask-image: radial-gradient(ellipse at center, black 30%, transparent 80%);
        }
      `}</style>
    </div>
  );
}
