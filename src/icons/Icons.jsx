import {
  LayoutDashboard, Files, Users, Activity, ShieldAlert, Scale, Network,
  FileBarChart, Settings, Plus, Search, Bell, LogOut,
  ChevronRight, ChevronDown, Check, X, Eye, Upload, Download,
  Filter, Pencil, Trash2, TriangleAlert, ArrowUp, ArrowDown,
  Clock, Mail, Building2, Phone, Briefcase, ChartNoAxesColumn,
  PieChart, Target, Cpu, Zap, Star, Info, Send, Copy, Layers,
  Globe, Gem, Bike, UserRound, UserCheck, UserRoundSearch,
  Car, Truck, Tractor, House, HousePlus, IdCard, Image,
  Landmark, ReceiptText, FileText, GraduationCap, Wheat,
  LockKeyhole, Circle, RefreshCw, ScanText, Banknote,
  ScrollText, Sparkles, BriefcaseBusiness, MapPinned,
  ClipboardList, CircleHelp, Menu, MessageSquare, BookOpenCheck,
} from "lucide-react";

const Icons = {
  dashboard: LayoutDashboard,
  apps: Files,
  chat: MessageSquare,
  compliance: BookOpenCheck,
  users: Users,
  risk: Activity,
  fraud: ShieldAlert,
  policy: Scale,
  report: FileBarChart,
  graph: Network,
  settings: Settings,
  plus: Plus,
  search: Search,
  bell: Bell,
  logout: LogOut,
  chevronR: ChevronRight,
  chevronD: ChevronDown,
  check: Check,
  x: X,
  eye: Eye,
  upload: Upload,
  download: Download,
  filter: Filter,
  edit: Pencil,
  trash: Trash2,
  alert: TriangleAlert,
  arrowUp: ArrowUp,
  arrowDown: ArrowDown,
  clock: Clock,
  mail: Mail,
  building: Building2,
  phone: Phone,
  briefcase: Briefcase,
  chart: ChartNoAxesColumn,
  pie: PieChart,
  target: Target,
  cpu: Cpu,
  zap: Zap,
  star: Star,
  info: Info,
  send: Send,
  copy: Copy,
  layers: Layers,
  globe: Globe,
  gem: Gem,
  bike: Bike,
  userRound: UserRound,
  userCheck: UserCheck,
  userSearch: UserRoundSearch,
  car: Car,
  truck: Truck,
  tractor: Tractor,
  house: House,
  housePlus: HousePlus,
  idCard: IdCard,
  image: Image,
  landmark: Landmark,
  receiptText: ReceiptText,
  fileText: FileText,
  graduation: GraduationCap,
  wheat: Wheat,
  lock: LockKeyhole,
  circle: Circle,
  refresh: RefreshCw,
  scanText: ScanText,
  banknote: Banknote,
  scrollText: ScrollText,
  sparkles: Sparkles,
  briefcaseBus: BriefcaseBusiness,
  mapPinned: MapPinned,
  clipboardList: ClipboardList,
  circleHelp: CircleHelp,
  menu: Menu,
};

function Icon({ d, size = 18, style, className, strokeWidth = 1.75, ...rest }) {
  if (typeof d === "string") {
    return (
      <svg
        width={size}
        height={size}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{ flexShrink: 0, ...style }}
        className={className}
      >
        <path d={d} />
      </svg>
    );
  }
  if (d) {
    const Comp = d;
    return (
      <Comp
        size={size}
        style={{ flexShrink: 0, ...style }}
        className={className}
        strokeWidth={strokeWidth}
        {...rest}
      />
    );
  }
  return null;
}

export { Icon, Icons };
