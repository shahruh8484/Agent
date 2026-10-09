import * as SecureStore from 'expo-secure-store';
import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from 'react';
import { Platform } from 'react-native';

import { setApiLang } from './api';

export type Lang = 'uz' | 'ru';

const ru = {
  tagline: 'Найдите проверенного мастера для любой задачи — или находите клиентов сами.',
  phone: 'Номер телефона',
  getCode: 'Получить код',
  codeSentTo: 'Код отправлен на {phone}',
  devCode: 'Тестовый режим: код {code}',
  smsCode: 'Код из SMS',
  signIn: 'Войти',
  changePhone: 'Изменить номер',
  networkError: 'Нет связи с сервером',
  inputError: 'Проверьте введённые данные',
  back: 'Назад',

  onboardingTitle: 'Знакомство',
  yourName: 'Как вас зовут?',
  namePlaceholder: 'Имя',
  city: 'Город',
  iWant: 'Я хочу',
  wantClient: 'Найти специалиста',
  wantClientText: 'Разместите заказ — специалисты сами предложат свои услуги.',
  wantSpecialist: 'Находить клиентов',
  wantSpecialistText: 'Откликайтесь на заказы по подписке. Первые дни — бесплатно.',
  continue: 'Продолжить',

  tabServices: 'Услуги',
  tabOrders: 'Заказы',
  tabMyOrders: 'Мои заказы',
  tabChats: 'Чаты',
  tabProfile: 'Профиль',

  searchService: 'Какая услуга нужна?',
  postOrder: '+ Разместить заказ',
  nothingFound: 'Ничего не нашлось',
  allInSection: 'Все специалисты раздела',
  specialists: 'Специалисты',
  postOrderHint: 'Разместить заказ — специалисты откликнутся сами',
  noSpecialists: 'В вашем городе пока нет специалистов в этом разделе. Разместите заказ — мы сообщим специалистам.',
  online: 'онлайн',
  worksOnline: 'работает онлайн',
  priceFrom: 'от {price}',
  experience: 'опыт {n} лет',
  noReviews: 'Нет отзывов',
  reviewsCount: '{n} отзывов',
  reviews: 'Отзывы',
  noReviewsYet: 'Пока нет отзывов',
  offerOrder: 'Предложить заказ',
  specialist: 'Специалист',

  newOrder: 'Новый заказ',
  service: 'Услуга',
  sections: '← Разделы',
  whatToDo: 'Что нужно сделать?',
  whatToDoPlaceholder: 'Например: репетитор по английскому для подготовки к IELTS',
  details: 'Подробности',
  detailsPlaceholder: 'Опишите задачу, пожелания, адрес или район',
  when: 'Когда',
  whenPlaceholder: 'Например: по вечерам, с 1 ноября',
  budget: 'Бюджет, сум',
  optional: 'Можно оставить пустым',
  remoteOk: 'Можно удалённо / онлайн',
  publish: 'Опубликовать заказ',

  order: 'Заказ',
  status_open: 'Ищем исполнителя',
  status_in_progress: 'В работе',
  status_completed: 'Выполнен',
  status_closed: 'Отменён',
  budgetLabel: 'Бюджет: {v}',
  whenLabel: 'Когда: {v}',
  whereLabel: 'Где: {v}',
  remoteSuffix: ', можно онлайн',
  clientLabel: 'Клиент: {v}',
  byAgreement: 'по договорённости',
  responsesCount: 'откликов: {n}',
  youResponded: 'Вы откликнулись · ',
  feed: 'Лента заказов',
  inWork: 'В работе',
  newOrderButton: '+ Новый заказ',
  emptyFeed: 'Подходящих заказов пока нет. Мы пришлём уведомление, когда появятся.',
  emptyFeedNoProfile: 'Заполните анкету специалиста в профиле, чтобы видеть заказы по своим услугам.',
  emptyAssigned: 'Здесь будут заказы, где клиент выбрал вас исполнителем.',
  emptyMine: 'У вас пока нет заказов. Разместите первый — специалисты откликнутся сами.',

  markDone: 'Работа выполнена',
  markDoneConfirm: 'Отметить заказ выполненным?',
  rateSpecialist: 'Оцените исполнителя',
  reviewPlaceholder: 'Расскажите, как всё прошло',
  leaveReview: 'Оставить отзыв',
  responses: 'Отклики ({n})',
  responsesSoon: 'Специалисты скоро откликнутся — мы пришлём уведомление.',
  price: 'Цена: {v}',
  chosen: 'Выбран исполнителем',
  write: 'Написать',
  choose: 'Выбрать',
  cancelOrder: 'Отменить заказ',
  cancelOrderConfirm: 'Отменить заказ?',
  yes: 'Да',
  no: 'Нет',
  yourResponse: 'Ваш отклик',
  clientChoseYou: 'Клиент выбрал вас!',
  openChat: 'Открыть чат с клиентом',
  noMoreResponses: 'Заказ больше не принимает отклики.',
  fillProfileToRespond: 'Заполнить анкету, чтобы откликаться',
  respond: 'Откликнуться',
  respondPlaceholder: 'Расскажите, как вы решите задачу, и задайте уточняющие вопросы',
  yourPrice: 'Ваша цена, сум',
  sendResponse: 'Отправить отклик',
  needSubscription: 'Для отклика нужна активная подписка.',

  chat: 'Чат',
  emptyChats: 'Здесь появятся переписки по вашим заказам и откликам.',
  user: 'Пользователь',
  message: 'Сообщение',

  mode: 'Режим',
  iAmClient: 'Я клиент',
  iAmSpecialist: 'Я специалист',
  forSpecialist: 'Для специалиста',
  subscription: 'Подписка',
  activeUntil: 'активна до {d}',
  notActive: 'не активна',
  profileFilled: 'Анкета заполнена',
  fillProfileHint: 'Заполните анкету, чтобы получать заказы',
  editProfile: 'Редактировать анкету',
  fillProfile: 'Заполнить анкету',
  plansButton: 'Тарифы и подписка',
  name: 'Имя',
  save: 'Сохранить',
  saved: 'Сохранено ✓',
  signOut: 'Выйти',
  language: 'Язык',

  specialistProfile: 'Анкета специалиста',
  myServices: 'Мои услуги ({n})',
  myServicesHint: 'Вы будете получать заказы по выбранным услугам.',
  about: 'О себе',
  aboutPlaceholder: 'Образование, опыт, чем вы лучше других',
  experienceYears: 'Опыт, лет',
  priceFromLabel: 'Цена от, сум',
  remoteWork: 'Работаю онлайн / по всему Узбекистану',
  saveProfile: 'Сохранить анкету',

  subscriptionTitle: 'Подписка специалиста',
  subscriptionText: 'Неограниченные отклики на заказы по всем вашим услугам. Без оплаты за каждый отклик.',
  subscriptionActive: 'Активна до {d}',
  subscriptionInactive: 'Подписка не активна',
  perMonth: '{v} в месяц',
  payWith: 'Оплатить через {p}',
  activate: 'Оформить',
  paymentPending: 'Ждём подтверждения оплаты…',
  months_one: '{n} месяц',
  months_few: '{n} месяца',
  months_many: '{n} месяцев',
  currency: 'сум',
};

type Key = keyof typeof ru;

const uz: Record<Key, string> = {
  tagline: "Har qanday ish uchun ishonchli usta toping — yoki o'zingiz mijoz toping.",
  phone: 'Telefon raqami',
  getCode: 'Kod olish',
  codeSentTo: 'Kod {phone} raqamiga yuborildi',
  devCode: 'Test rejimi: kod {code}',
  smsCode: 'SMS kod',
  signIn: 'Kirish',
  changePhone: "Raqamni o'zgartirish",
  networkError: "Server bilan aloqa yo'q",
  inputError: "Kiritilgan ma'lumotlarni tekshiring",
  back: 'Orqaga',

  onboardingTitle: 'Tanishuv',
  yourName: 'Ismingiz?',
  namePlaceholder: 'Ism',
  city: 'Shahar',
  iWant: 'Men',
  wantClient: 'Mutaxassis topmoqchiman',
  wantClientText: "Buyurtma joylang — mutaxassislar o'zlari xizmat taklif qiladi.",
  wantSpecialist: 'Mijoz topmoqchiman',
  wantSpecialistText: 'Obuna orqali buyurtmalarga taklif yuboring. Dastlabki kunlar — bepul.',
  continue: 'Davom etish',

  tabServices: 'Xizmatlar',
  tabOrders: 'Buyurtmalar',
  tabMyOrders: 'Buyurtmalarim',
  tabChats: 'Chatlar',
  tabProfile: 'Profil',

  searchService: 'Qanday xizmat kerak?',
  postOrder: '+ Buyurtma joylash',
  nothingFound: 'Hech narsa topilmadi',
  allInSection: "Bo'limdagi barcha mutaxassislar",
  specialists: 'Mutaxassislar',
  postOrderHint: "Buyurtma joylang — mutaxassislar o'zlari taklif yuboradi",
  noSpecialists: "Shahringizda bu bo'limda hozircha mutaxassis yo'q. Buyurtma joylang — mutaxassislarga xabar beramiz.",
  online: 'onlayn',
  worksOnline: 'onlayn ishlaydi',
  priceFrom: '{price} dan',
  experience: 'tajriba {n} yil',
  noReviews: "Sharhlar yo'q",
  reviewsCount: '{n} ta sharh',
  reviews: 'Sharhlar',
  noReviewsYet: "Hozircha sharhlar yo'q",
  offerOrder: 'Buyurtma taklif qilish',
  specialist: 'Mutaxassis',

  newOrder: 'Yangi buyurtma',
  service: 'Xizmat',
  sections: "← Bo'limlar",
  whatToDo: 'Nima qilish kerak?',
  whatToDoPlaceholder: 'Masalan: IELTS ga tayyorlash uchun ingliz tili repetitori',
  details: 'Batafsil',
  detailsPlaceholder: 'Vazifa, istaklar, manzil yoki tumanni yozing',
  when: 'Qachon',
  whenPlaceholder: "Masalan: kechqurun, 1-noyabrdan",
  budget: "Byudjet, so'm",
  optional: "Bo'sh qoldirish mumkin",
  remoteOk: 'Masofadan / onlayn mumkin',
  publish: "Buyurtmani e'lon qilish",

  order: 'Buyurtma',
  status_open: 'Ijrochi qidirilmoqda',
  status_in_progress: 'Bajarilmoqda',
  status_completed: 'Bajarildi',
  status_closed: 'Bekor qilindi',
  budgetLabel: 'Byudjet: {v}',
  whenLabel: 'Qachon: {v}',
  whereLabel: 'Qayerda: {v}',
  remoteSuffix: ', onlayn mumkin',
  clientLabel: 'Mijoz: {v}',
  byAgreement: 'kelishiladi',
  responsesCount: 'takliflar: {n}',
  youResponded: 'Siz taklif yubordingiz · ',
  feed: 'Buyurtmalar lentasi',
  inWork: 'Bajarilmoqda',
  newOrderButton: '+ Yangi buyurtma',
  emptyFeed: "Hozircha mos buyurtmalar yo'q. Paydo bo'lganda xabar beramiz.",
  emptyFeedNoProfile: "Xizmatlaringiz bo'yicha buyurtmalarni ko'rish uchun profilda mutaxassis anketasini to'ldiring.",
  emptyAssigned: 'Bu yerda mijoz sizni ijrochi qilib tanlagan buyurtmalar chiqadi.',
  emptyMine: "Sizda hali buyurtmalar yo'q. Birinchisini joylang — mutaxassislar o'zlari taklif yuboradi.",

  markDone: 'Ish bajarildi',
  markDoneConfirm: 'Buyurtma bajarildi deb belgilansinmi?',
  rateSpecialist: 'Ijrochini baholang',
  reviewPlaceholder: "Hammasi qanday o'tganini yozing",
  leaveReview: 'Sharh qoldirish',
  responses: 'Takliflar ({n})',
  responsesSoon: 'Mutaxassislar tez orada taklif yuboradi — xabar beramiz.',
  price: 'Narx: {v}',
  chosen: 'Ijrochi sifatida tanlangan',
  write: 'Yozish',
  choose: 'Tanlash',
  cancelOrder: 'Buyurtmani bekor qilish',
  cancelOrderConfirm: 'Buyurtma bekor qilinsinmi?',
  yes: 'Ha',
  no: "Yo'q",
  yourResponse: 'Sizning taklifingiz',
  clientChoseYou: 'Mijoz sizni tanladi!',
  openChat: 'Mijoz bilan chatni ochish',
  noMoreResponses: 'Buyurtma endi taklif qabul qilmaydi.',
  fillProfileToRespond: "Taklif yuborish uchun anketani to'ldiring",
  respond: 'Taklif yuborish',
  respondPlaceholder: "Vazifani qanday bajarishingizni yozing va savollaringizni bering",
  yourPrice: "Sizning narxingiz, so'm",
  sendResponse: 'Taklifni yuborish',
  needSubscription: 'Taklif yuborish uchun faol obuna kerak.',

  chat: 'Chat',
  emptyChats: "Bu yerda buyurtma va takliflaringiz bo'yicha yozishmalar chiqadi.",
  user: 'Foydalanuvchi',
  message: 'Xabar',

  mode: 'Rejim',
  iAmClient: 'Men mijozman',
  iAmSpecialist: 'Men mutaxassisman',
  forSpecialist: 'Mutaxassis uchun',
  subscription: 'Obuna',
  activeUntil: '{d} gacha faol',
  notActive: 'faol emas',
  profileFilled: "Anketa to'ldirilgan",
  fillProfileHint: "Buyurtmalar olish uchun anketani to'ldiring",
  editProfile: 'Anketani tahrirlash',
  fillProfile: "Anketani to'ldirish",
  plansButton: 'Tariflar va obuna',
  name: 'Ism',
  save: 'Saqlash',
  saved: 'Saqlandi ✓',
  signOut: 'Chiqish',
  language: 'Til',

  specialistProfile: 'Mutaxassis anketasi',
  myServices: 'Xizmatlarim ({n})',
  myServicesHint: "Tanlangan xizmatlar bo'yicha buyurtmalar olasiz.",
  about: "O'zim haqimda",
  aboutPlaceholder: "Ma'lumotingiz, tajribangiz, boshqalardan nimasi bilan yaxshisiz",
  experienceYears: 'Tajriba, yil',
  priceFromLabel: "Narx, so'mdan",
  remoteWork: "Onlayn / butun O'zbekiston bo'ylab ishlayman",
  saveProfile: 'Anketani saqlash',

  subscriptionTitle: 'Mutaxassis obunasi',
  subscriptionText: "Barcha xizmatlaringiz bo'yicha buyurtmalarga cheksiz taklif yuborish. Har bir taklif uchun to'lov yo'q.",
  subscriptionActive: '{d} gacha faol',
  subscriptionInactive: 'Obuna faol emas',
  perMonth: 'oyiga {v}',
  payWith: "{p} orqali to'lash",
  activate: 'Rasmiylashtirish',
  paymentPending: "To'lov tasdiqlanishini kutmoqdamiz…",
  months_one: '{n} oy',
  months_few: '{n} oy',
  months_many: '{n} oy',
  currency: "so'm",
};

const dictionaries: Record<Lang, Record<Key, string>> = { ru, uz };

export type T = (key: Key, vars?: Record<string, string | number>) => string;

const LANG_KEY = 'servio_lang';

interface LangState {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: T;
}

const LangContext = createContext<LangState | null>(null);

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>('uz');
  const [ready, setReady] = useState(false);

  useEffect(() => {
    (Platform.OS === 'web' ? Promise.resolve(null) : SecureStore.getItemAsync(LANG_KEY))
      .then((saved) => {
        if (saved === 'ru' || saved === 'uz') setLangState(saved);
        setApiLang(saved === 'ru' ? 'ru' : 'uz');
      })
      .finally(() => setReady(true));
  }, []);

  const setLang = useCallback((next: Lang) => {
    setApiLang(next);
    setLangState(next);
    if (Platform.OS !== 'web') SecureStore.setItemAsync(LANG_KEY, next).catch(() => undefined);
  }, []);

  const t = useCallback<T>((key, vars) => {
    let text = dictionaries[lang][key] ?? ru[key];
    if (vars) for (const [k, v] of Object.entries(vars)) text = text.replace(`{${k}}`, String(v));
    return text;
  }, [lang]);

  if (!ready) return null;
  return <LangContext.Provider value={{ lang, setLang, t }}>{children}</LangContext.Provider>;
}

export function useT(): LangState {
  const ctx = useContext(LangContext);
  if (!ctx) throw new Error('useT outside LangProvider');
  return ctx;
}

/** "1 месяц / 3 месяца / 12 месяцев" in Russian, "1 oy" in Uzbek. */
export function monthsLabel(t: T, n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return t('months_one', { n });
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return t('months_few', { n });
  return t('months_many', { n });
}

const MONTHS: Record<Lang, string[]> = {
  ru: ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'],
  uz: ['yan', 'fev', 'mar', 'apr', 'may', 'iyun', 'iyul', 'avg', 'sen', 'okt', 'noy', 'dek'],
};

export function formatDate(iso: string | null, lang: Lang): string {
  if (!iso) return '';
  const d = new Date(iso);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${d.getDate()} ${MONTHS[lang][d.getMonth()]}, ${hh}:${mm}`;
}

/** 99000 → "99 000 so'm"; null → "kelishiladi". */
export function formatPrice(t: T, value: number | null | undefined): string {
  if (value == null) return t('byAgreement');
  return `${formatNumber(value)} ${t('currency')}`;
}

export function formatNumber(value: number): string {
  return String(Math.round(value)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
}
