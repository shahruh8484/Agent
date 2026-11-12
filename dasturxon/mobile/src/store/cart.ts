import { create } from 'zustand';
import type { MenuDish } from '../api/types';

export interface CartLine {
  dish: Pick<MenuDish, 'id' | 'name' | 'price' | 'photoUrls' | 'portionsLeft'>;
  qty: number;
}

interface CartState {
  chefId: number | null;
  chefNick: string | null;
  lines: CartLine[];
  /** стоимость доставки, показанная в карточке повара (уточняется на чекауте) */
  deliveryFee: number | null;
  setChef: (chefId: number, chefNick: string, deliveryFee: number | null) => void;
  add: (dish: CartLine['dish']) => void;
  remove: (dishId: number) => void;
  setLines: (chefId: number, chefNick: string, lines: CartLine[], deliveryFee?: number | null) => void;
  clear: () => void;
}

export const useCart = create<CartState>((set, get) => ({
  chefId: null,
  chefNick: null,
  lines: [],
  deliveryFee: null,

  setChef: (chefId, chefNick, deliveryFee) => {
    if (get().chefId !== chefId) set({ chefId, chefNick, lines: [], deliveryFee });
    else set({ chefNick, deliveryFee: deliveryFee ?? get().deliveryFee });
  },

  add: (dish) => {
    const lines = [...get().lines];
    const i = lines.findIndex((l) => l.dish.id === dish.id);
    if (i >= 0) {
      const max = dish.portionsLeft ?? 50;
      lines[i] = { ...lines[i], qty: Math.min(lines[i].qty + 1, max) };
    } else lines.push({ dish, qty: 1 });
    set({ lines });
  },

  remove: (dishId) => {
    const lines = get()
      .lines.map((l) => (l.dish.id === dishId ? { ...l, qty: l.qty - 1 } : l))
      .filter((l) => l.qty > 0);
    set({ lines });
  },

  setLines: (chefId, chefNick, lines, deliveryFee = null) => set({ chefId, chefNick, lines, deliveryFee }),

  clear: () => set({ chefId: null, chefNick: null, lines: [], deliveryFee: null }),
}));

export const cartCount = (lines: CartLine[]) => lines.reduce((s, l) => s + l.qty, 0);
export const cartTotal = (lines: CartLine[]) => lines.reduce((s, l) => s + l.qty * l.dish.price, 0);
