import { errorMessage } from '../api/client';
import { publicApi } from '../api/endpoints';
import type { OrderItem } from '../api/types';
import type { Nav } from '../navigation/types';
import { useCart } from '../store/cart';
import { confirmDialog, showAlert } from './dialog';

/** «Повторить заказ»: собираем корзину из прошлого заказа по актуальному меню повара */
export async function repeatOrder(nav: Nav, chefId: number, items: OrderItem[]) {
  try {
    const [chef, menu] = await Promise.all([publicApi.chef(chefId), publicApi.menu(chefId)]);
    if (!menu.available) {
      showAlert('Повар сейчас не принимает заказы', 'Попробуйте позже или выберите другого повара.');
      nav.navigate('Chef', { chefId });
      return;
    }
    const lines = items
      .map((it) => {
        const d = menu.dishes.find((x) => x.id === it.dishId);
        if (!d) return null;
        const qty = d.portionsLeft != null ? Math.min(it.qty, d.portionsLeft) : it.qty;
        return qty > 0 ? { dish: { id: d.id, name: d.name, price: d.price, photoUrls: d.photoUrls, portionsLeft: d.portionsLeft }, qty } : null;
      })
      .filter((l): l is NonNullable<typeof l> => !!l);
    if (!lines.length) {
      showAlert('Блюд из заказа больше нет', 'Посмотрите актуальное меню повара.');
      nav.navigate('Chef', { chefId });
      return;
    }
    const cart = useCart.getState();
    if (cart.lines.length && cart.chefId !== chefId) {
      const ok = await confirmDialog('Очистить корзину?', `В корзине блюда от повара ${cart.chefNick}. Заменить их блюдами из этого заказа?`, 'Заменить', 'Отмена', true);
      if (!ok) return;
    }
    useCart.getState().setLines(chefId, chef.nick, lines, chef.deliveryFee);
    if (lines.length < items.length) showAlert('Часть блюд недоступна', 'Мы добавили в корзину только те блюда, что есть в меню сейчас.');
    nav.navigate('Chef', { chefId });
  } catch (e) {
    showAlert('Не удалось повторить заказ', errorMessage(e));
  }
}
