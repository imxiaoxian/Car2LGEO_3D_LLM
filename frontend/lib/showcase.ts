export const showcaseCars = [
  { make: "Ferrari", model: "F40", year: 1990, image: "/images/ferrari-f40.webp", color: "#c72826", set: "76934", subtitle: "经典红色。永恒轮廓。" },
  { make: "Porsche", model: "911", year: 1989, image: "/images/porsche-911.webp", color: "#d9d9d7", set: "10295", subtitle: "一眼经典，每一块亦然。" },
  { make: "McLaren", model: "P1", year: 2015, image: "/images/mclaren-p1.webp", color: "#e9ae28", set: "42172", subtitle: "性能之美，逐块呈现。" },
];

export const viewNames: Record<string, string> = {
  left:"左侧",right:"右侧",
  back:"后视",top:"顶视",front_left:"左前斜视",front_right:"右前斜视",rear_left:"左后斜视",rear_right:"右后斜视",
  windscreen:"风挡与立柱",door_left_closed:"左门关闭",door_right_closed:"右门关闭",
  door_left_open:"左门开启",door_right_open:"右门开启",headlamps:"前灯组",taillamps:"尾灯组",
  front_cabin_join:"车头与座舱连接",rear_cabin_join:"座舱与车尾连接",seats_pedals:"座椅与座舱",
  pedal_footwell:"踏板与脚舱",seat_mounts:"座椅安装与滑轨",
  front_running_gear:"前轮与转向",rear_running_gear:"后轮与悬挂",front_cover_open:"前盖开启",
  rear_cover_open:"后盖开启",powertrain_cargo:"动力舱与储物舱",scissor_hinge:"剪刀门铰链",
  side_intake:"侧进气口",popup_lamps:"跳灯开启",rear_clamshell:"整体后掀车壳",rear_doors:"后排车门",
  render_front:"零件前视",render_rear:"零件后视",render_left:"零件左视",render_right:"零件右视",render_top:"零件顶视",render_bottom:"零件底视",
  render_left_door_window:"左门与车窗",render_right_door_window:"右门与车窗",
  render_front_lights:"前灯组装配",render_rear_lights:"尾灯组装配",
  render_cabin_front_connection:"车头与座舱接缝",render_cabin_rear_connection:"座舱与车尾接缝",
  render_front_wheel:"前轮安装",render_rear_wheel:"后轮安装",
  render_front_left:"零件左前斜视",render_rear_right:"零件右后斜视",
  assembly: "装配模型",
  assembly_rear: "模型后视",
  overview: "整车", chassis: "底盘", front: "前部", body: "车身", rear: "尾部", wheels: "车轮",
  engine_bay: "发动机舱", engine_details: "发动机细节", cockpit: "仪表台与内饰", seats: "座椅细节",
  doors: "车门开合", hood_opening: "前盖与后盖开合", steering: "转向细节", suspension: "悬挂与转向",
  dashboard: "仪表台特写", door_hinges: "车门铰链与间隙", hood_hinges: "前后盖铰链与间隙",
  underbody: "底盘结构特写", rear_seats: "后排座舱", cargo_bay: "储物与检修空间",
  side_intakes: "侧进气口", lights: "灯组细节",
};

export const categoryNames: Record<string, string> = {
  aerodynamics: "空气动力", wheels_stance: "轮毂与姿态", exhaust: "排气",
  body_kits: "车身套件", interior: "内饰", lighting: "灯光", paint_finish: "配色", performance: "性能",
};
