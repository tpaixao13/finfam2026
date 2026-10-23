// Ícones Bootstrap (via react-icons). Os services do backend devolvem nomes "bi-*";
// este mapa os converte em componentes.
import type { IconType } from 'react-icons'
import {
  BsArrowDownCircle, BsArrowLeft, BsArrowRepeat, BsArrowUpCircle, BsBag, BsBagHeart, BsBank, BsBarChartLine,
  BsBell, BsBellFill, BsBook, BsBoxArrowRight, BsBuilding, BsCalendarCheck, BsCalendarEvent, BsCalendarRange,
  BsCarFront, BsCart3, BsCashCoin, BsCheck2, BsCheck2Circle, BsCheckAll, BsCheckCircleFill, BsChevronLeft,
  BsChevronRight, BsClockHistory, BsCloudUpload, BsCoin, BsController, BsCreditCard, BsCreditCard2Front,
  BsDownload, BsExclamationCircle, BsExclamationOctagon, BsFlag, BsFlagFill, BsGear, BsGraphDownArrow,
  BsGraphUp, BsGraphUpArrow, BsHeartPulse, BsHourglassSplit, BsHouse, BsInfoCircle, BsKey, BsLightning,
  BsList, BsListUl, BsPencil, BsPeopleFill, BsPersonFill, BsPhone, BsPieChart, BsPieChartFill, BsPiggyBank,
  BsPlayCircle, BsPlusLg, BsReceipt, BsRocket, BsRocketTakeoff, BsSearch, BsShield, BsShieldCheck,
  BsSpeedometer2, BsStars, BsTag, BsTags, BsTelegram, BsThreeDotsVertical, BsTools, BsTrash3, BsTrophy,
  BsTrophyFill, BsWallet2, BsWifi, BsX, BsXCircle, BsXCircleFill,
} from 'react-icons/bs'

const MAP: Record<string, IconType> = {
  'arrow-down-circle': BsArrowDownCircle, 'arrow-left': BsArrowLeft, 'arrow-repeat': BsArrowRepeat,
  'arrow-up-circle': BsArrowUpCircle, bag: BsBag, 'bag-heart': BsBagHeart, bank: BsBank,
  'bar-chart-line': BsBarChartLine, bell: BsBell, 'bell-fill': BsBellFill, book: BsBook,
  'box-arrow-right': BsBoxArrowRight, building: BsBuilding, 'calendar-check': BsCalendarCheck,
  'calendar-event': BsCalendarEvent, 'calendar-range': BsCalendarRange, 'car-front': BsCarFront,
  cart3: BsCart3, 'cash-coin': BsCashCoin, check2: BsCheck2, 'check2-circle': BsCheck2Circle,
  'check-all': BsCheckAll, 'check-circle-fill': BsCheckCircleFill, 'chevron-left': BsChevronLeft,
  'chevron-right': BsChevronRight, 'clock-history': BsClockHistory, 'cloud-upload': BsCloudUpload,
  coin: BsCoin, controller: BsController, 'credit-card': BsCreditCard, 'credit-card-2-front': BsCreditCard2Front,
  download: BsDownload, 'exclamation-circle': BsExclamationCircle, 'exclamation-octagon': BsExclamationOctagon,
  flag: BsFlag, 'flag-fill': BsFlagFill, gear: BsGear, 'graph-down-arrow': BsGraphDownArrow,
  'graph-up': BsGraphUp, 'graph-up-arrow': BsGraphUpArrow, 'heart-pulse': BsHeartPulse,
  'hourglass-split': BsHourglassSplit, house: BsHouse, 'info-circle': BsInfoCircle, key: BsKey,
  lightning: BsLightning, list: BsList, 'list-ul': BsListUl, pencil: BsPencil, 'people-fill': BsPeopleFill,
  'person-fill': BsPersonFill, phone: BsPhone, 'pie-chart': BsPieChart, 'pie-chart-fill': BsPieChartFill,
  'piggy-bank': BsPiggyBank, 'play-circle': BsPlayCircle, plus: BsPlusLg, receipt: BsReceipt, rocket: BsRocket,
  'rocket-takeoff': BsRocketTakeoff, search: BsSearch, shield: BsShield, 'shield-check': BsShieldCheck,
  speedometer2: BsSpeedometer2, stars: BsStars, tag: BsTag, tags: BsTags, telegram: BsTelegram,
  'three-dots-vertical': BsThreeDotsVertical, tools: BsTools, trash: BsTrash3, trophy: BsTrophy,
  'trophy-fill': BsTrophyFill, wallet2: BsWallet2, wifi: BsWifi, x: BsX, 'x-circle': BsXCircle,
  'x-circle-fill': BsXCircleFill,
}

export function Icon({ name, size, className, title }: { name: string; size?: number; className?: string; title?: string }) {
  const Cmp = MAP[name.replace(/^bi-/, '')] ?? BsTag
  return <Cmp size={size} className={className} aria-hidden={title ? undefined : true} title={title} />
}

export const CATEGORY_ICONS: Record<string, string> = {
  'Alimentação': 'cart3', Transporte: 'car-front', 'Saúde': 'heart-pulse', Lazer: 'controller',
  Moradia: 'house', 'Educação': 'book', 'Vestuário': 'bag', 'Serviços': 'tools', Compras: 'bag-heart',
  Beleza: 'stars', Internet: 'wifi', Telefone: 'phone', Outros: 'tag',
}
