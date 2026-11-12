import bcrypt from 'bcryptjs';
import { fileURLToPath } from 'node:url';
import { one, pool, query } from './db.js';
import { migrate } from './migrate.js';

// Демо-данные: сотрудники админки, три повара в Ташкенте с меню и тестовый покупатель.
// Повторный запуск безопасен (ON CONFLICT).
export async function seed() {
  const ownerPass = process.env.SEED_OWNER_PASSWORD ?? 'admin12345';
  const staff = [
    ['Владелец', 'owner@dasturxon.uz', ownerPass, 'owner'],
    ['Бухгалтер', 'accountant@dasturxon.uz', 'accountant123', 'accountant'],
    ['Поддержка', 'support@dasturxon.uz', 'support123', 'support'],
  ];
  for (const [name, email, pass, role] of staff) {
    await query('INSERT INTO admin_staff (name, email, password_hash, role) VALUES ($1,$2,$3,$4) ON CONFLICT (email) DO NOTHING',
      [name, email, await bcrypt.hash(pass, 12), role]);
  }

  const chefs = [
    { phone: '+998901110001', nick: 'Нилуфар', first: 'Нилуфар', last: 'Каримова', district: 'Яшнабад', lat: 41.2995, lon: 69.3166,
      bio: 'Готовлю по рецептам бабушки из Ферганы. Плов только на казане.',
      dishes: [
        ['Плов по-фергански', 'Говядина, рис девзира, морковь', 'lunch', 35000, '400 г', 'ready', null],
        ['Лагман', 'Домашняя лапша, говядина, овощи', 'lunch', 28000, '350 г', 'both', 40],
        ['Манты (8 шт)', 'Говядина с луком, на пару', 'dinner', 22000, '8 шт', 'order', 60],
      ] },
    { phone: '+998901110002', nick: 'Малика', first: 'Малика', last: 'Юсупова', district: 'Чиланзар', lat: 41.2756, lon: 69.2034,
      bio: 'Выпечка и самса из тандыра каждый день с 8 утра.',
      dishes: [
        ['Самса с мясом (6 шт)', 'Слоёное тесто, баранина, лук', 'bakery', 28000, '6 шт', 'ready', null],
        ['Чучвара', 'Мелкие пельмени с бульоном', 'dinner', 25000, '300 г', 'order', 45],
        ['Сырники', 'Со сметаной и вареньем', 'breakfast', 18000, '4 шт', 'ready', null],
      ] },
    { phone: '+998901110003', nick: 'Азиза', first: 'Азиза', last: 'Рахимова', district: 'Юнусабад', lat: 41.3653, lon: 69.2847,
      bio: 'Вегетарианская и лёгкая домашняя кухня.',
      dishes: [
        ['Овощной шурпа', 'Нут, картофель, морковь, зелень', 'lunch', 20000, '400 мл', 'ready', null],
        ['Ачичук с лепёшкой', 'Томаты, лук, лепёшка из тандыра', 'other', 12000, '250 г', 'ready', null],
      ] },
  ];
  const img = (seed: string) => `https://picsum.photos/seed/${encodeURIComponent(seed)}/800/450`;
  for (const c of chefs) {
    const u = await one<any>(
      `INSERT INTO users (phone, role, name) VALUES ($1,'chef',$2) ON CONFLICT (phone) DO UPDATE SET name = EXCLUDED.name RETURNING id`,
      [c.phone, `${c.first} ${c.last}`]);
    await query(
      `INSERT INTO chef_profiles (user_id, nick, first_name, last_name, bio, photo_url, cover_url, district, address_text, address_lat, address_lon,
         is_online, is_verified, verification_status, payout_card_token, payout_card_masked, card_type, terms_accepted_at, kitchen_photo_urls)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,TRUE,TRUE,'approved',$12,$13,'uzcard',now(),$14)
       ON CONFLICT (user_id) DO NOTHING`,
      [u.id, c.nick, c.first, c.last, c.bio, img(`chef-${c.nick}`), img(`cover-${c.nick}`), c.district, `${c.district}, демо-адрес`, c.lat, c.lon,
        `mock_card_${c.nick}`, `8600 **** **** ${String(u.id).padStart(4, '0')}`, [img(`kitchen-${c.nick}`)]]);
    const has = await one('SELECT 1 FROM dishes WHERE chef_id = $1', [u.id]);
    if (!has) {
      for (const [name, desc, cat, price, weight, type, prep] of c.dishes) {
        await query(
          `INSERT INTO dishes (chef_id, name, description, category, price, weight, photo_urls, type, prep_time_minutes, is_vegan)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`,
          [u.id, name, desc, cat, price, weight, [img(String(name))], type, prep, c.nick === 'Азиза']);
      }
    }
  }

  const buyer = await one<any>(`INSERT INTO users (phone, role, name) VALUES ('+998901234567','buyer','Тестовый покупатель')
                                ON CONFLICT (phone) DO UPDATE SET name = EXCLUDED.name RETURNING id`);
  const hasAddr = await one('SELECT 1 FROM addresses WHERE user_id = $1', [buyer.id]);
  if (!hasAddr) {
    await query(`INSERT INTO addresses (user_id, label, lat, lon, address_text, is_default) VALUES ($1,'Дом',41.3111,69.2797,'Ташкент, Мирабадский р-н, ул. Амира Темура 1',TRUE)`, [buyer.id]);
    await query(`INSERT INTO payment_cards (user_id, provider, token, masked_pan, card_type, is_default) VALUES ($1,'payme','mock_demo','8600 **** **** 4242','uzcard',TRUE)`, [buyer.id]);
  }
  console.log('[seed] готово: owner@dasturxon.uz / ' + ownerPass + ', покупатель +998901234567, повара +99890111000{1,2,3}');
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  migrate().then(seed).then(() => pool.end()).catch((e) => { console.error(e); process.exit(1); });
}
