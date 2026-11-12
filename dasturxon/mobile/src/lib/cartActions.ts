import { useCart, type CartLine } from '../store/cart';
import { toast } from '../store/toast';
import { confirmDialog } from './dialog';

/** Добавить блюдо в корзину. В корзине может быть только один повар (ТЗ 3.3). */
export async function addToCart(chef: { id: number; nick: string; deliveryFee?: number | null }, dish: CartLine['dish']): Promise<boolean> {
  const cart = useCart.getState();
  if (cart.chefId && cart.chefId !== chef.id && cart.lines.length) {
    const ok = await confirmDialog(
      'Очистить корзину?',
      `В корзине блюда от повара ${cart.chefNick}. Заказ можно оформить только у одного повара. Очистить корзину и добавить блюдо?`,
      'Очистить',
      'Отмена',
      true,
    );
    if (!ok) return false;
    cart.clear();
  }
  const line = useCart.getState().lines.find((l) => l.dish.id === dish.id);
  if (line && dish.portionsLeft != null && line.qty >= dish.portionsLeft) {
    toast(`Осталось только ${dish.portionsLeft} порц.`);
    return false;
  }
  useCart.getState().setChef(chef.id, chef.nick, chef.deliveryFee ?? null);
  useCart.getState().add(dish);
  return true;
}
