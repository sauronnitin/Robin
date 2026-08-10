import type { FeaturedSkill } from "lib/redux/types";

/** Minimal slice of Open Resume resumeSlice initial featured skills. */
export const initialFeaturedSkill: FeaturedSkill = { skill: "", rating: 4 };
export const initialFeaturedSkills: FeaturedSkill[] = Array(6).fill({
  ...initialFeaturedSkill,
});
