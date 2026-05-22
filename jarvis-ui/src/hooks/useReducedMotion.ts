/**
 * Thin re-export of Framer Motion's `useReducedMotion` so call sites
 * import from one place. Lets us swap in a global override later
 * (e.g. a Settings toggle that forces motion on for users whose OS
 * setting they want to bypass) without touching every consumer.
 */
export { useReducedMotion } from "framer-motion";
