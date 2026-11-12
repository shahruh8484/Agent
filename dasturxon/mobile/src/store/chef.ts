import { create } from 'zustand';
import { chefApi } from '../api/endpoints';
import type { ChefProfile } from '../api/types';

interface ChefState {
  profile: ChefProfile | null;
  load: () => Promise<ChefProfile>;
  setProfile: (p: ChefProfile) => void;
  reset: () => void;
}

export const useChef = create<ChefState>((set) => ({
  profile: null,
  load: async () => {
    const profile = await chefApi.profile();
    set({ profile });
    return profile;
  },
  setProfile: (profile) => set({ profile }),
  reset: () => set({ profile: null }),
}));

/** Шаг регистрации повара (ТЗ 4.1): 1–2 профиль, 3 фото кухни, 4 карта, 'waiting' — на проверке, 'done' */
export type ChefStep = 1 | 3 | 4 | 'waiting' | 'done';

export function chefStep(p: ChefProfile | null): ChefStep {
  if (!p || p.registrationStep || !p.nick) return 1;
  const st = p.verificationStatus;
  if (st === 'draft' || st === 'rejected' || !st) return 3;
  if (!p.payoutCard || !p.termsAccepted) return 4;
  if (st === 'pending') return 'waiting';
  return 'done';
}
