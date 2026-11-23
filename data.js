/* =============================================================================
 * data.js —— SubFlow Studio 静态数据层
 * 1) LANGUAGES_12   视频翻译的 12 种语言（含示例句）
 * 2) UI_LANGS       界面语言的 12 种（含 RTL 标记）
 * 3) I18N           12 套界面文案
 * 4) INDUSTRIES_36  36 个行业 × 24 条术语（紧凑格式 "原文=译文"，运行时解析）
 * ========================================================================== */

/* ---------- 1) 翻译语种 ---------- */
const LANGUAGES_12 = [
  { code: 'zh-CN', short: 'ZH', name: '简体中文', sample: '当动力总成 CAN总线 出现 显性电平 锁死故障时，' },
  { code: 'zh-TW', short: 'TW', name: '繁體中文', sample: '當動力總成 CAN匯流排 出現 顯性電位 鎖死故障時，' },
  { code: 'en', short: 'EN', name: 'English 英语', sample: 'When the powertrain CAN Bus experiences a dominant state lockup fault,' },
  { code: 'ja', short: 'JA', name: '日本語 日语', sample: 'パワートレインのCANバスにドミナント固定故障が発生した場合、' },
  { code: 'ko', short: 'KO', name: '한국어 韩语', sample: '파워트레인 CAN 버스에 우성 레벨 잠김 고장이 발생하면,' },
  { code: 'de', short: 'DE', name: 'Deutsch 德语', sample: 'Wenn der Antriebsstrang-CAN-Bus einen dominanten Pegelblockierfehler aufweist,' },
  { code: 'fr', short: 'FR', name: 'Français 法语', sample: 'Lorsque le bus CAN du groupe motopropulseur subit un verrouillage dominant,' },
  { code: 'es', short: 'ES', name: 'Español 西语', sample: 'Cuando el bus CAN del tren motriz experimenta un bloqueo de estado dominante,' },
  { code: 'ru', short: 'RU', name: 'Русский 俄语', sample: 'Когда шина CAN силовой установки блокируется в доминантном состоянии,' },
  { code: 'pt', short: 'PT', name: 'Português 葡语', sample: 'Quando o barramento CAN do trem de força sofre uma falha dominante,' },
  { code: 'it', short: 'IT', name: 'Italiano 意语', sample: 'Quando il bus CAN del propulsore subisce un blocco dominante,' },
  { code: 'ar', short: 'AR', name: 'العربية 阿语', sample: 'عندما يواجه ناقل CAN الخاص بنظام الدفع خطأ قفل الحالة المهيمنة،' }
];

/* ---------- 2) 界面语言（12 种） ---------- */
const UI_LANGS = [
  { code: 'zh-CN', name: '简体中文', short: '简', dir: 'ltr' },
  { code: 'zh-TW', name: '繁體中文', short: '繁', dir: 'ltr' },
  { code: 'en', name: 'English', short: 'EN', dir: 'ltr' },
  { code: 'ja', name: '日本語', short: '日', dir: 'ltr' },
  { code: 'ko', name: '한국어', short: '한', dir: 'ltr' },
  { code: 'de', name: 'Deutsch', short: 'DE', dir: 'ltr' },
  { code: 'fr', name: 'Français', short: 'FR', dir: 'ltr' },
  { code: 'es', name: 'Español', short: 'ES', dir: 'ltr' },
  { code: 'ru', name: 'Русский', short: 'RU', dir: 'ltr' },
  { code: 'pt', name: 'Português', short: 'PT', dir: 'ltr' },
  { code: 'it', name: 'Italiano', short: 'IT', dir: 'ltr' },
  { code: 'ar', name: 'العربية', short: 'AR', dir: 'rtl' }
];

/* ---------- 3) 12 套界面文案 ---------- */
const I18N = {
  'zh-CN': {
    undo: '撤销', islandCollapsedHint: '点击导入视频或切换互译语种',
    sourceLang: '源语言', targetLang: '译为', startTranslate: '提取并翻译', importVideo: '导入本地视频', importSrt: '导入本地字幕',
    preferences: '偏好设置', exportBtn: '导出成片', visParticles: '星尘引力', visStrings: '零延迟琴弦', visCanbus: '双线差分波',
    splitBtn: '拆分', mergeBtn: '合并', tabSubs: '双语字幕流', tabStyle: '触控造型台', tabTerms: '术语魔方',
    xyPadTitle: '二维磁吸触控垫 (拖动光球实时塑形)', fontSize: '主字幕字号', bilingualOrder: '双语上下排布',
    subColor: '主字幕霓虹色', colorLab: '调色台',
    timelineTitle: '流体惯性声纹时间轴', timelineHint: '点击或搓动轨道体验弹簧阻尼滑行指针 · 划过声纹触发 18% 柔音泛音',
    insertSeg: '+ 插入新字幕段', prefHeader: '偏好设置'
  },
  'zh-TW': {
    undo: '復原', islandCollapsedHint: '點擊匯入影片或切換互譯語種',
    sourceLang: '來源語言', targetLang: '譯為', startTranslate: '提取並翻譯', importVideo: '匯入本機影片', importSrt: '匯入本機字幕',
    preferences: '偏好設定', exportBtn: '匯出成片', visParticles: '星塵引力', visStrings: '零延遲琴弦', visCanbus: '雙線差分波',
    splitBtn: '拆分', mergeBtn: '合併', tabSubs: '雙語字幕流', tabStyle: '觸控造型台', tabTerms: '術語魔方',
    xyPadTitle: '二維磁吸觸控墊 (拖動光球即時塑形)', fontSize: '主字幕字級', bilingualOrder: '雙語上下排布',
    subColor: '主字幕霓虹色', colorLab: '調色台',
    timelineTitle: '流體慣性聲紋時間軸', timelineHint: '點擊或搓動軌道體驗彈簧阻尼滑行指針',
    insertSeg: '+ 插入新字幕段', prefHeader: '偏好設定'
  },
  'en': {
    undo: 'Undo', islandCollapsedHint: 'Click to import media or switch languages',
    sourceLang: 'From', targetLang: 'To', startTranslate: 'Translate', importVideo: 'Import Video', importSrt: 'Import .SRT',
    preferences: 'Preferences', exportBtn: 'Export', visParticles: 'Gravity Dust', visStrings: 'Zero-Lag Strings', visCanbus: 'Diff Waveform',
    splitBtn: 'Split', mergeBtn: 'Merge', tabSubs: 'Subtitles', tabStyle: '2D Stylizer', tabTerms: 'Glossary',
    xyPadTitle: '2D Magnetic XY Pad (drag puck to style)', fontSize: 'Main Size', bilingualOrder: 'Track Order',
    subColor: 'Neon Color', colorLab: 'Color Lab',
    timelineTitle: 'Fluid Inertia Timeline', timelineHint: 'Scrub the track for a spring-damped playhead & soft harmonics',
    insertSeg: '+ Add Segment', prefHeader: 'Preferences'
  },
  'ja': {
    undo: '元に戻す', islandCollapsedHint: '動画の読込・翻訳言語の切替',
    sourceLang: '原語', targetLang: '翻訳先', startTranslate: 'AI翻訳開始', importVideo: 'ローカル動画', importSrt: 'SRT字幕読込',
    preferences: '環境設定', exportBtn: '書き出し', visParticles: '星屑重力場', visStrings: 'ゼロ遅延弦', visCanbus: '差動波形',
    splitBtn: '分割', mergeBtn: '結合', tabSubs: '対訳字幕', tabStyle: '2Dスタイル', tabTerms: '専門用語集',
    xyPadTitle: '2Dマグネットパッド (ドラッグで調整)', fontSize: '主字幕サイズ', bilingualOrder: '上下配置',
    subColor: '字幕カラー', colorLab: 'カラーラボ',
    timelineTitle: '慣性波形タイムライン', timelineHint: 'トラック操作で滑らかな慣性ポインターを体験',
    insertSeg: '+ 字幕を追加', prefHeader: '環境設定'
  },
  'ko': {
    undo: '실행 취소', islandCollapsedHint: '클릭하여 영상 가져오기 또는 언어 전환',
    sourceLang: '원문 언어', targetLang: '번역', startTranslate: '추출 및 번역', importVideo: '로컬 영상', importSrt: '자막 가져오기',
    preferences: '환경설정', exportBtn: '내보내기', visParticles: '성진 중력', visStrings: '제로 지연 현', visCanbus: '차동 파형',
    splitBtn: '분할', mergeBtn: '병합', tabSubs: '이중 자막', tabStyle: '스타일 패드', tabTerms: '용어 사전',
    xyPadTitle: '2D 자석 터치 패드 (드래그로 조정)', fontSize: '주 자막 크기', bilingualOrder: '자막 순서',
    subColor: '네온 색상', colorLab: '컬러 랩',
    timelineTitle: '관성 파형 타임라인', timelineHint: '트랙을 드래그해 스프링 감쇠 포인터를 느껴보세요',
    insertSeg: '+ 자막 추가', prefHeader: '환경설정'
  },
  'de': {
    undo: 'Rückgängig', islandCollapsedHint: 'Klicken zum Importieren oder Sprachwechsel',
    sourceLang: 'Ausgangssprache', targetLang: 'Zielsprache', startTranslate: 'Extrahieren & Übersetzen', importVideo: 'Video importieren',
    importSrt: 'Untertitel importieren', preferences: 'Einstellungen', exportBtn: 'Exportieren',
    visParticles: 'Sternenstaub', visStrings: 'Null-Latenz-Saiten', visCanbus: 'Differenzwelle',
    splitBtn: 'Teilen', mergeBtn: 'Zusammenführen', tabSubs: 'Untertitel', tabStyle: 'Stil-Pad', tabTerms: 'Glossar',
    xyPadTitle: '2D-Magnetpad (zum Gestalten ziehen)', fontSize: 'Hauptgröße', bilingualOrder: 'Reihenfolge',
    subColor: 'Neonfarbe', colorLab: 'Farblabor',
    timelineTitle: 'Trägheits-Timeline', timelineHint: 'Ziehen für federnde Wiedergabe & sanfte Obertöne',
    insertSeg: '+ Segment', prefHeader: 'Einstellungen'
  },
  'fr': {
    undo: 'Annuler', islandCollapsedHint: 'Cliquez pour importer ou changer de langue',
    sourceLang: 'Langue source', targetLang: 'Langue cible', startTranslate: 'Extraire et traduire', importVideo: 'Importer une vidéo',
    importSrt: 'Importer un SRT', preferences: 'Préférences', exportBtn: 'Exporter',
    visParticles: 'Poussière stellaire', visStrings: 'Cordes sans latence', visCanbus: 'Onde différentielle',
    splitBtn: 'Diviser', mergeBtn: 'Fusionner', tabSubs: 'Sous-titres', tabStyle: 'Style', tabTerms: 'Glossaire',
    xyPadTitle: 'Pavé magnétique 2D (glisser pour styliser)', fontSize: 'Taille principale', bilingualOrder: 'Ordre des pistes',
    subColor: 'Couleur néon', colorLab: 'Labo couleur',
    timelineTitle: "Chronologie d'inertie", timelineHint: 'Glissez la piste pour un curseur à ressort',
    insertSeg: '+ Segment', prefHeader: 'Préférences'
  },
  'es': {
    undo: 'Deshacer', islandCollapsedHint: 'Haz clic para importar o cambiar idioma',
    sourceLang: 'Idioma origen', targetLang: 'Idioma destino', startTranslate: 'Extraer y traducir', importVideo: 'Importar vídeo',
    importSrt: 'Importar subtítulos', preferences: 'Preferencias', exportBtn: 'Exportar',
    visParticles: 'Polvo estelar', visStrings: 'Cuerdas sin latencia', visCanbus: 'Onda diferencial',
    splitBtn: 'Dividir', mergeBtn: 'Fusionar', tabSubs: 'Subtítulos', tabStyle: 'Panel de estilo', tabTerms: 'Glosario',
    xyPadTitle: 'Pad magnético 2D (arrastra para estilizar)', fontSize: 'Tamaño principal', bilingualOrder: 'Orden de pistas',
    subColor: 'Color neón', colorLab: 'Laboratorio de color',
    timelineTitle: 'Línea de tiempo inercial', timelineHint: 'Arrastra la pista para un cursor con resorte',
    insertSeg: '+ Segmento', prefHeader: 'Preferencias'
  },
  'ru': {
    undo: 'Отменить', islandCollapsedHint: 'Нажмите, чтобы импортировать или сменить язык',
    sourceLang: 'Исходный язык', targetLang: 'Язык перевода', startTranslate: 'Извлечь и перевести', importVideo: 'Импорт видео',
    importSrt: 'Импорт субтитров', preferences: 'Настройки', exportBtn: 'Экспорт',
    visParticles: 'Звёздная пыль', visStrings: 'Струны без задержки', visCanbus: 'Дифференциальная волна',
    splitBtn: 'Разделить', mergeBtn: 'Объединить', tabSubs: 'Субтитры', tabStyle: 'Стиль', tabTerms: 'Глоссарий',
    xyPadTitle: '2D-пад (перетащите для стиля)', fontSize: 'Основной размер', bilingualOrder: 'Порядок дорожек',
    subColor: 'Неоновый цвет', colorLab: 'Цветовая лаборатория',
    timelineTitle: 'Инерционная шкала', timelineHint: 'Тяните дорожку для пружинного курсора',
    insertSeg: '+ Сегмент', prefHeader: 'Настройки'
  },
  'pt': {
    undo: 'Desfazer', islandCollapsedHint: 'Clique para importar ou trocar idioma',
    sourceLang: 'Idioma de origem', targetLang: 'Idioma de destino', startTranslate: 'Extrair e traduzir', importVideo: 'Importar vídeo',
    importSrt: 'Importar legendas', preferences: 'Preferências', exportBtn: 'Exportar',
    visParticles: 'Poeira estelar', visStrings: 'Cordas sem latência', visCanbus: 'Onda diferencial',
    splitBtn: 'Dividir', mergeBtn: 'Mesclar', tabSubs: 'Legendas', tabStyle: 'Estilo', tabTerms: 'Glossário',
    xyPadTitle: 'Pad magnético 2D (arraste para estilizar)', fontSize: 'Tamanho principal', bilingualOrder: 'Ordem das faixas',
    subColor: 'Cor neon', colorLab: 'Laboratório de cor',
    timelineTitle: 'Linha do tempo inercial', timelineHint: 'Arraste a faixa para um cursor com mola',
    insertSeg: '+ Segmento', prefHeader: 'Preferências'
  },
  'it': {
    undo: 'Annulla', islandCollapsedHint: 'Clicca per importare o cambiare lingua',
    sourceLang: 'Lingua di origine', targetLang: 'Lingua di destinazione', startTranslate: 'Estrai e traduci', importVideo: 'Importa video',
    importSrt: 'Importa sottotitoli', preferences: 'Preferenze', exportBtn: 'Esporta',
    visParticles: 'Polvere stellare', visStrings: 'Corde senza latenza', visCanbus: 'Onda differenziale',
    splitBtn: 'Dividi', mergeBtn: 'Unisci', tabSubs: 'Sottotitoli', tabStyle: 'Stile', tabTerms: 'Glossario',
    xyPadTitle: 'Pad magnetico 2D (trascina per stilizzare)', fontSize: 'Dimensione principale', bilingualOrder: 'Ordine tracce',
    subColor: 'Colore neon', colorLab: 'Laboratorio colori',
    timelineTitle: 'Timeline inerziale', timelineHint: 'Trascina la traccia per un cursore a molla',
    insertSeg: '+ Segmento', prefHeader: 'Preferenze'
  },
  'ar': {
    undo: 'تراجع', islandCollapsedHint: 'انقر لاستيراد الفيديو أو تبديل اللغة',
    sourceLang: 'اللغة المصدر', targetLang: 'اللغة الهدف', startTranslate: 'استخراج وترجمة', importVideo: 'استيراد فيديو',
    importSrt: 'استيراد ترجمة', preferences: 'الإعدادات', exportBtn: 'تصدير',
    visParticles: 'غبار النجوم', visStrings: 'أوتار بلا تأخير', visCanbus: 'موجة تفاضلية',
    splitBtn: 'تقسيم', mergeBtn: 'دمج', tabSubs: 'الترجمة', tabStyle: 'لوحة التنسيق', tabTerms: 'المصطلحات',
    xyPadTitle: 'لوحة سحرية ثنائية الأبعاد (اسحب للتنسيق)', fontSize: 'حجم الترجمة', bilingualOrder: 'ترتيب الأسطر',
    subColor: 'لون النيون', colorLab: 'مختبر الألوان',
    timelineTitle: 'الخط الزمني القصوري', timelineHint: 'اسحب المسار لمؤشر نابضي',
    insertSeg: '+ مقطع', prefHeader: 'الإعدادات'
  }
};

/* ---------- 4) 36 行业术语库（紧凑格式） ---------- */
const IND_RAW = [
  [0, 0, "汽车电控与CAN", "CAN Bus=CAN总线|dominant state=显性电平|recessive state=隐性电平|ECU=电控单元|OBD-II=车载诊断系统|termination resistor=终端电阻|oscilloscope=示波器|drivetrain=动力总成|differential voltage=差分电压|baud rate=波特率|LIN Bus=LIN局域网总线|FlexRay=高速容错总线|DTC=故障诊断码|PWM=脉冲宽度调制|Gateway=车载网关|UDS Protocol=统一诊断服务|CRC Check=循环冗余校验|Arbitration ID=帧仲裁ID|Bus-off=总线关闭状态|Stuff bit=填充位|ACK Slot=应答间隙|Frame format=帧格式|Twisted pair=双绞屏蔽线|Transceiver=总线收发器"],
  [1, 0, "新能源三电系统", "BMS=电池管理系统|VCU=整车控制器|MCU=电机控制器|SOC=荷电状态(SOC)|SOH=电池健康度|Thermal runaway=热失控|Liquid cooling=液冷循环|Cell balancing=电芯均衡|IGBT=绝缘栅双极型晶体管|SiC MOSFET=碳化硅功率管|Regenerative braking=动能回收制动|Permanent magnet=永磁同步电机|Stator winding=定子绕组|Hairpin winding=发卡式绕组|Inverter=逆变器总成|DC-DC Converter=直流变换器|OBC=车载充电机|HVIL=高压互锁回路|Insulation resistance=绝缘电阻|Pre-charge relay=预充继电器|Main contactor=主接触器|Busbar=高压铜排|LFP Battery=磷酸铁锂电池|Solid-state battery=全固态电池"],
  [2, 0, "智驾与激光雷达", "LiDAR=激光雷达|Millimeter-wave=毫米波雷达|BEV Perception=鸟瞰图感知|Occupancy Network=占用栅格网络|SLAM=同步定位与建图|Point cloud=三维点云|Sensor fusion=多传感器融合|ACC=自适应巡航|AEB=自动紧急制动|LKA=车道保持辅助|NOA=导航辅助驾驶|End-to-End=端到端智驾模型|Corner case=极端边缘场景|HD Map=高精地图|Odometry=视觉里程计|Kalman filter=卡尔曼滤波|Trajectory planning=轨迹规划|MPC Controller=模型预测控制|Steer-by-wire=线控转向|Brake-by-wire=线控制动|Redundancy=双冗余架构|Orin-X=智驾算力芯片|Time-of-Flight=飞行时间测距|Solid-state LiDAR=固态激光雷达"],
  [3, 0, "智能座舱与车机", "HUD=抬头显示系统|AR-HUD=增强现实抬头显示|SoC=座舱系统级芯片|Hypervisor=虚拟机监控程序|QNX=实时车载操作系统|OTA Update=空中固件升级|Voice wake-up=语音唤醒率|Multi-zone audio=多音区声源定位|OLED Cluster=全液晶仪表盘|Ambient lighting=律动氛围灯|DMS=驾驶员监测系统|OMS=乘员监测系统|Gaze tracking=视线追踪|Capacitive touch=电容触控反馈|CarPlay=车载投屏互联|Automotive Ethernet=车载以太网|SOME/IP=面向服务中间件|LVDS=低压差分信号|A2B Bus=车载音频总线|Active Noise Cancel=主动降噪(ANC)|Spatial audio=全景声场|Cold boot=车机冷启动|Frame drop=界面掉帧|HMI Design=人机交互界面"],
  [4, 0, "底盘悬架与动力学", "Air suspension=空气悬架|CDC Damper=连续阻尼控制减振|Double wishbone=双叉臂独立悬架|Multi-link=多连杆悬架|MacPherson=麦弗逊悬架|Camber angle=车轮外倾角|Caster angle=主销后倾角|Toe-in=前束角|Unsprung mass=簧下质量|Anti-roll bar=防倾杆/稳定杆|Subframe=副车架|Bushing=液压衬套|Understeer=转向不足(推头)|Oversteer=转向过度(甩尾)|Torque vectoring=扭矩矢量分配|LSD=限滑差速器|Ackermann geometry=阿克曼转向几何|Roll center=侧倾中心|Pitching=车身俯仰|NVH Performance=噪声振动与声振粗糙度|Brake caliper=制动卡钳|Ventilated disc=通风刹车盘|ESP=车身电子稳定系统|Steering knuckle=转向节(羊角)"],
  [5, 0, "汽修诊断与保养", "Misfire=气缸失火|Compression test=缸压测试|Fuel trim=燃油修正值|Oxygen sensor=氧传感器|Catalytic converter=三元催化器|Throttle body=节气门体|Carbon deposit=积碳清洗|Timing belt=正时皮带|Head gasket=气缸垫冲床|Coolant flush=防冻液循环更换|Brake fluid=制动液含水量|Wheel alignment=四轮定位|Dynamic balance=动平衡配重|Spark plug gap=火花塞间隙|Ignition coil=点火线圈|Fuel injector=喷油嘴雾化|Crankshaft sensor=曲轴位置传感器|Camshaft sensor=凸轮轴位置传感器|Parasitic draw=静态暗电流漏电|Multimeter=数字万用表|Smoke tester=烟雾测漏仪|Torque wrench=扭力扳手|Thread locker=螺纹紧固胶|Borescope=内窥镜探查"],

  [6, 1, "AI大模型与LLM", "Quantization=模型量化|Context Window=上下文窗口|Fine-tuning=微调训练|Inference=模型推理|Hallucination=幻觉现象|Token=词元(Token)|Embedding=向量嵌入|RAG=检索增强生成|Zero-shot=零样本推理|Transformer=Transformer架构|Self-Attention=自注意力机制|LoRA=低秩自适应|RLHF=人类反馈强化学习|MoE=混合专家模型|KV Cache=键值缓存|FlashAttention=闪存注意力|Perplexity=困惑度|Temperature=采样温度|Top-p=核采样|Chain of Thought=思维链(CoT)|Agentic Workflow=智能体工作流|Multimodal=多模态|Distillation=知识蒸馏|Overfitting=过拟合"],
  [7, 1, "计算机视觉(CV)", "Bounding box=边界框|Semantic segmentation=语义分割|Instance segmentation=实例分割|Convolutional layer=卷积层|ResNet=残差网络|ViT=视觉Transformer|Optical flow=光流估计|Keypoint detection=关键点检测|Super-resolution=超分辨率重建|Diffusion model=扩散生成模型|Denoising=去噪采样|Latent space=潜在空间|CLIP=图文对比预训练|NeRF=神经辐射场|3D Gaussian Splatting=三维高斯泼溅|IoU=交并比指标|NMS=非极大值抑制|Data augmentation=数据增强|Feature map=特征图|Backbone=主干提取网络|Anchor-free=无锚框检测|Pose estimation=姿态估计|Depth estimation=单目深度估计|Edge detection=边缘检测算子"],
  [8, 1, "语音识别与NLP", "ASR=自动语音识别|TTS=语音合成|VAD=语音活动检测|Mel-spectrogram=梅尔频谱图|Phoneme=音素单元|Word Error Rate=词错误率(WER)|Beam search=束搜索解码|Speaker diarization=说话人分离|Voice cloning=音色克隆|Prosody=韵律与语调|Tokenization=分词算法|Named Entity=命名实体识别|Sentiment analysis=情感倾向分析|Seq2Seq=序列到序列|Cross-entropy=交叉熵损失|BLEU Score=机器翻译评估分|Zero-pronoun=零代词消解|Glossary lock=术语强制锁定|Sliding window=上下文滑动窗口|Timestamp alignment=时间戳强制对齐|Homophone=同音词纠错|Acoustic model=声学模型|Language model=语言模型|Forced alignment=音文强制对齐"],
  [9, 1, "强化学习与具身", "Reward function=奖励函数|Policy gradient=策略梯度|Q-Learning=价值学习|Markov Decision=马尔可夫决策|Exploration=探索与利用|Sim-to-Real=仿真到真实迁移|Embodied AI=具身智能|Humanoid robot=人形机器人|End-effector=末端执行器|Inverse kinematics=逆运动学求解|Proprioception=本体感知|Tactile sensor=触觉阵列传感器|Locomotion=双足运动控制|Dexterous hand=灵巧手操控|Imitation learning=模仿学习|Behavior cloning=行为克隆|World model=世界模型|Teleoperation=遥操作采集|Actuator=关节执行器|Zero Moment Point=零力矩点(ZMP)|Gait planning=步态规划|Collision avoidance=动态避障|Force feedback=力反馈控制|ROS2=机器人操作系统"],
  [10, 1, "Python与桌面开发", "PyWebView=原生Web窗口容器|Subprocess=子进程通信|Multithreading=多线程并发|Asyncio=异步事件循环|GIL=全局解释器锁|Virtualenv=虚拟隔离环境|PyInstaller=可执行文件打包|Inno Setup=安装包封装构建|FFmpeg pipe=音视频管道流|Garbage collection=垃圾内存回收|Memory leak=内存泄漏排查|Exception handling=异常捕获兜底|Type hinting=类型注解|Decorator=装饰器模式|Context manager=上下文管理器|JSON serialization=JSON序列化|RESTful API=标准接口规范|WebSocket=全双工长连接|SQLite=轻量本地数据库|Hardware ID=硬件机器指纹|License verification=卡密授权校验|Hot reload=热重载调试|Cross-platform=跨平台兼容|DPI Scaling=高分屏缩放适配"],
  [11, 1, "网络安全与逆向", "Zero-day=零日漏洞|Buffer overflow=缓冲区溢出|SQL Injection=SQL注入攻击|XSS=跨站脚本攻击|CSRF=跨站请求伪造|Man-in-the-Middle=中间人攻击|TLS Handshake=TLS加密握手|Public key=公钥基础设施|Reverse engineering=逆向工程|Decompiler=反编译器|Obfuscation=代码混淆加固|Sandboxing=沙箱隔离运行|Penetration test=渗透测试|Firewall=防火墙规则|Payload=攻击载荷|Shellcode=注入指令码|Privilege escalation=权限提升|Digital signature=数字签名校验|SHA-256=哈希摘要算法|AES Encryption=对称加密标准|Token expiration=令牌过期机制|Rate limiting=接口限流防御|Honeypot=蜜罐诱捕系统|Vulnerability=安全脆弱点"],

  [12, 2, "数控机床与CNC", "5-Axis CNC=五轴联动数控|Spindle speed=主轴转速|Feed rate=进给速率|G-Code=G代码刀路|Toolpath=刀具轨迹规划|Milling cutter=硬质合金铣刀|Lathe turning=车削加工|Surface roughness=表面粗糙度|Tolerance=尺寸公差|Runout=主轴径向跳动|Ball screw=精密滚珠丝杠|Linear guide=直线导轨|Tool changer=自动换刀装置(ATC)|Coolant nozzle=高压切削液喷嘴|Chip evacuation=排屑器系统|Zero point=工件坐标系零点|Fixture clamping=工装夹具装夹|Backlash=丝杠反向间隙|Chatter vibration=切削颤振|Roughing=粗加工开粗|Finishing=精加工光刀|Chamfering=倒角去毛刺|EDM=电火花加工|CMM=三坐标测量机"],
  [13, 2, "液压与气动传动", "Hydraulic cylinder=液压油缸|Proportional valve=电液比例阀|Servo valve=高频响伺服阀|Axial piston pump=轴向柱塞泵|Relief valve=溢流安全阀|Check valve=单向止回阀|Accumulator=液压蓄能器|Cavitation=气蚀现象|Water hammer=水锤冲击效应|Bulk modulus=体积弹性模量|Dynamic seal=动密封O型圈|Manifold block=液压集成阀块|Pressure drop=管路压力损失|Flow rate=体积流量|Viscosity=液压油运动粘度|Pneumatic actuator=气动执行器|Solenoid valve=电磁换向阀|FRL Unit=气源三联件|Vacuum ejector=真空发生器|Quick coupling=快换接头|Load sensing=负载敏感系统|Closed-loop control=闭环压力控制|Hydraulic schematic=液压原理图|Oil contamination=油液颗粒污染度"],
  [14, 2, "工业机器人与PLC", "PLC=可编程逻辑控制器|Ladder diagram=梯形图编程|SCADA=数据采集与监视系统|HMI Touchpanel=工业触摸屏|Servo drive=伺服驱动器|Incremental encoder=增量式编码器|Absolute encoder=绝对值编码器|Harmonic reducer=谐波减速机|RV Reducer=RV摆线针轮减速机|Six-axis robot=六轴关节机器人|SCARA Robot=水平多关节机器人|Delta Robot=并联分拣机器人|Teach pendant=示教器手柄|TCP Calibration=工具中心点标定|EtherCAT=工业实时以太网|Modbus RTU=串口通信协议|Profinet=西门子工业总线|PID Tuning=PID参数整定|Limit switch=行程限位开关|Photoelectric sensor=光电接近开关|Safety light curtain=安全光幕|Emergency stop=急停安全回路|Palletizing=自动化码垛|Cycle time=产线节拍时间"],
  [15, 2, "模具设计与冲压", "Injection molding=注塑成型模具|Die casting=高压压铸模具|Progressive die=级进连续冲压模|Cavity & Core=型腔与型芯(公母模)|Parting line=分型面设计|Draft angle=拔模斜度|Ejector pin=顶针脱模机构|Slider mechanism=滑块侧抽芯|Lifter=斜顶机构|Hot runner=热流道系统|Gate location=浇口位置优化|Weld line=熔接痕缺陷|Sink mark=缩水凹痕|Warpage=翘曲变形|Venting groove=排气槽深度|Cooling channel=随形冷却水路|Blank holder=压边圈力|Springback=冲压回弹补偿|Sheet metal=钣金折弯展开|Deep drawing=深拉伸工艺|Mold flow=模流分析仿真|Clamping force=锁模力吨位|Cycle cooling=保压冷却时间|Mirror polishing=镜面火花抛光"],
  [16, 2, "材料科学与热处理", "Tensile strength=抗拉强度|Yield strength=屈服强度|Young's modulus=杨氏弹性模量|Fatigue limit=疲劳寿命极限|Fracture toughness=断裂韧性|Quenching=淬火硬化|Tempering=回火去应力|Annealing=退火软化|Carburizing=渗碳表面硬化|Nitriding=渗氮处理|Induction hardening=高频感应淬火|Martensite=马氏体金相|Austenite=奥氏体转变|Grain boundary=晶界强化|Rockwell hardness=洛氏硬度(HRC)|Vickers hardness=维氏硬度|Anodizing=阳极氧化膜|Electroplating=电镀防腐层|PVD Coating=物理气相沉积涂层|Carbon fiber=碳纤维复合材料|Titanium alloy=钛合金切削|Dielectric strength=介电击穿强度|Thermal conductivity=导热系数|Creep resistance=高温抗蠕变"],
  [17, 2, "增材制造(3D打印)", "SLM=选区激光熔化(金属)|SLA=立体光固化成型|FDM=熔融沉积成型|SLS=选区激光烧结|Layer thickness=打印层厚精度|Support structure=悬空支撑结构|Infill density=内部填充密度|Slicing software=三维切片软件|G-code path=喷头扫描路径|Overhang angle=极限悬垂角|Warping adhesion=翘边与热床附着|Retraction=抽丝回抽设置|Nozzle diameter=喷嘴口径|Metal powder=球形金属粉末|Laser power=激光功率密度|Scan strategy=棋盘格扫描策略|Residual stress=残余热应力|Post-processing=后处理打磨固化|HIP=热等静压致密化|Topology optimization=拓扑轻量化设计|Lattice structure=点阵晶格结构|Build plate=成型基板|Resin vat=感光树脂槽|Dimensional accuracy=成型尺寸精度"],

  [18, 3, "GPU架构与CUDA", "VRAM=显存容量|CUDA Core=CUDA流处理器|Tensor Core=张量计算核心|RT Core=光线追踪核心|Streaming Multiprocessor=流多处理器(SM)|Warp scheduler=线程束调度器|Shared memory=片上共享内存|Memory bandwidth=显存位宽与带宽|L2 Cache=二级大容量缓存|NVLink=高速显卡互联|PCIe Gen5=第五代PCIe通道|FP16 / BF16=半精度浮点运算|INT8 Quantization=8位整数量化|NVENC=硬件视频编码器|NVDEC=硬件视频解码器|Kernel launch=核函数并行启动|Thread block=线程块网格|Bank conflict=存储体访问冲突|Coalesced access=合并内存访问|Occupancy=计算资源占用率|Thermal throttling=过热降频保护|TDP=热设计功耗|Boost clock=动态加速频率|Undervolting=降压超频曲线"],
  [19, 3, "半导体光刻与晶圆", "EUV Lithography=极紫外光刻机|DUV Immersion=浸没式深紫外光刻|Silicon wafer=单晶硅晶圆|Photoresist=光刻胶涂覆|Photomask=光刻掩模版|Etching=等离子干法刻蚀|CVD=化学气相沉积|ALD=原子层沉积|Ion implantation=离子注入掺杂|CMP=化学机械抛光|FinFET=鳍式场效应晶体管|GAAFET=全环绕栅极纳米片|High-NA=高数值孔径镜头|Cleanroom=超净间等级|Yield rate=晶圆良率|Die size=裸片面积|Chiplet=芯粒异构集成|CoWoS Packaging=2.5D先进封装|TSV=硅通孔技术|Wafer dicing=晶圆切割划片|Wire bonding=引线键合|Flip-chip=倒装芯片焊球|Moore's Law=摩尔定律演进|Leakage current=亚阈值漏电流"],
  [20, 3, "嵌入式MCU与RTOS", "ARM Cortex-M=Cortex-M微控制器|FreeRTOS=实时多任务内核|Task preemption=任务抢占调度|Interrupt latency=中断响应延迟|ISR=中断服务例程|DMA Controller=直接内存访问|Watchdog timer=看门狗定时器|GPIO=通用输入输出引脚|Pull-up resistor=上拉电阻|UART=通用异步串口|I2C Bus=双线串行总线|SPI Interface=高速同步串行接口|ADC Sampling=模数转换采样|DAC Output=数模转换输出|PWM Duty cycle=PWM占空比|Bootloader=引导加载程序|OTA Firmware=固件差分升级|Flash memory=片上闪存扇区|SRAM=静态随机内存|Stack overflow=任务栈溢出|Mutex lock=互斥锁同步|Semaphore=信号量机制|Low-power sleep=深度休眠功耗|JTAG / SWD=硬件在线调试器"],
  [21, 3, "PCB高速布线设计", "Multilayer PCB=多层印制电路板|Impedance matching=特征阻抗匹配|Differential pair=差分走线等长|Via stitching=接地过孔缝合|Blind/Buried via=盲孔与埋孔|Signal integrity=信号完整性(SI)|Power integrity=电源完整性(PI)|Crosstalk=邻线串扰抑制|Return path=高频回流路径|Ground plane=完整参考地平面|Decoupling capacitor=退耦滤波电容|Serpentine trace=蛇形走线绕等长|BGA Fanout=BGA封装扇出|Solder mask=阻焊绿油层|Silkscreen=丝印字符层|Copper pour=敷铜网格|DRC Check=设计规则检查|Gerber files=制板光绘文件|SMT Assembly=表面贴装贴片|Reflow soldering=回流焊温度曲线|Wave soldering=波峰焊工艺|AOI Inspection=自动光学检测|X-Ray BGA=X光虚焊检测|EMC / EMI=电磁兼容与辐射"],
  [22, 3, "射频通信与天线", "RF Transceiver=射频收发信机|Power Amplifier=射频功率放大器(PA)|LNA=低噪声放大器|Mixer=混频器本振|PLL Synthesizer=锁相环频率合成|S-Parameters=散射参数(S参数)|Return loss=回波损耗(S11)|VSWR=电压驻波比|Smith chart=史密斯圆图阻抗|Vector Network Analyzer=矢量网络分析仪|Spectrum analyzer=频谱分析仪|MIMO Antenna=多输入多输出天线|Beamforming=波束赋形相控阵|Phased array=相控阵雷达单元|Microstrip patch=微带贴片天线|Attenuation=信道衰减损耗|Multipath fading=多径衰落效应|QAM Modulation=正交幅度调制|OFDM=正交频分复用|Signal-to-Noise=信噪比(SNR)|Noise figure=噪声系数(NF)|Harmonic distortion=谐波失真|Coaxial connector=同轴射频连接器|Anechoic chamber=微波暗室测试"],
  [23, 3, "电源管理与电力电子", "Buck converter=降压斩波电路|Boost converter=升压变换器|LDO Regulator=低压差线性稳压|Flyback topology=反激式开关电源|LLC Resonant=LLC谐振半桥|PFC Circuit=功率因数校正|GaN Charger=氮化镓快充功率管|Switching frequency=开关频率损耗|Ripple voltage=输出纹波电压|Transient response=负载瞬态响应|Soft start=软启动防浪涌|Over-current protection=过流保护(OCP)|Over-voltage protection=过压保护(OVP)|Thermal shutdown=过温关断保护|Inductor saturation=电感磁饱和电流|Synchronous rectification=同步整流技术|Zero Voltage Switching=零电压软开关(ZVS)|Dead time=上下管死区时间|Gate driver=栅极驱动芯片|Bootstrap capacitor=自举升压电容|Isolation transformer=高频隔离变压器|Optocoupler=光耦反馈隔离|Efficiency curve=轻重载转换效率|Quiescent current=静态待机电流"],

  [24, 4, "影视后期与调色", "Timecode=SMPTE时间码|Keyframe=关键帧曲线|Color grading=一级/二级调色|Color correction=色彩还原校正|LUT=色彩查找表|Log Footage=Log灰片素材|Dynamic range=动态范围宽容度|Rec.709=标准高清色域|DCI-P3=数字影院广色域|ACES Workflow=学院色彩编码系统|Waveform monitor=亮度波形示波器|Vectorscope=矢量色彩示波器|False color=伪色曝光辅助|Chroma key=绿幕色度抠像|Rotoscoping=逐帧边缘遮罩|Motion tracking=摄像机反求跟踪|Optical flow=光流法补帧慢放|Proxy workflow=代理剪辑工作流|XML / EDL=时间线工程套底|Hardcoded sub=烧录硬压字幕|ASS Subtitle=高级样式字幕轨|Jump cut=跳切剪辑节奏|L-Cut / J-Cut=声画错位剪辑|Render queue=批量渲染队列"],
  [25, 4, "音频工程与混音", "Sample rate=采样率(48kHz)|Bit depth=量化位深(24-bit)|LUFS=响度标准化单位|Dynamic compressor=动态范围压缩器|Limiter=峰值砖墙限制器|Equalizer (EQ)=参数均衡器|High-pass filter=高通低切滤波|De-esser=齿音消除器|Noise gate=噪声门限|Reverb=空间混响效果|Sidechain=侧链闪避压缩|Vocal isolation=AI人声伴奏分离|Clipping distortion=削波爆音失真|Phase cancellation=相位抵消问题|Phantom power=48V幻象电源|Cardioid mic=心形指向麦克风|XLR Interface=卡农平衡接口|Audio interface=专业外置声卡|DAW=数字音频工作站|VST Plugin=虚拟效果器插件|Stem mastering=分轨母带处理|Signal-to-Noise=本底信噪比|Pop filter=防喷罩|Foley sound=拟音音效制作"],
  [26, 4, "3D建模与Blender", "Polygon mesh=多边形网格拓扑|Subdivision surface=细分曲面修改器|Bevel modifier=倒角高光修改器|Boolean operation=布尔切割运算|UV Unwrapping=UV展平拆分|Normal map=法线凹凸贴图|Roughness map=粗糙度贴图|PBR Material=基于物理渲染材质|Shader nodes=着色器节点连线|Cycles Render=路径追踪渲染器|EEVEE=实时光栅渲染引擎|Global illumination=全局光照反弹|Ambient occlusion=环境光遮蔽(AO)|HDRI Lighting=高动态全景环境光|Rigging=骨骼绑定权重|Inverse Kinematics=IK反向动力学|Shape keys=形态键面部表情|Weight painting=骨骼权重绘制|Particle system=毛发与粒子系统|Fluid simulation=流体烟雾解算|Cloth physics=布料碰撞物理|Geometry Nodes=几何节点程序化|Retopology=高模重拓扑低模|Baking textures=烘焙法线与光照"],
  [27, 4, "虚幻引擎(UE5)开发", "Nanite=虚拟微多边形几何|Lumen=全动态全局光照|Blueprints=蓝图可视化编程|Niagara=次世代粒子特效|MetaHuman=高保真数字人类|Sequencer=非线性动画过场|Landscape=超大地形雕刻|Foliage tool=植被生态散布|World Partition=世界分区流式加载|Virtual Shadow Maps=虚拟阴影贴图|Temporal Super Res=TSR时域超分|Ray tracing=硬件光线追踪|Material instance=材质实例动态参数|Post-process volume=后期处理体积框|Collision preset=碰撞通道预设|Animation Montage=动画蒙太奇状态机|Blend space=二维混合空间|Control Rig=引擎内骨骼控制器|Live Link=动捕实时驱动|Draw call=渲染批次调用|Shader compilation=着色器编译卡顿|LOD=多细节层次模型|Lightmass=静态光照烘焙|Packaging build=项目打包发布"],
  [28, 4, "摄影光学与器材", "Full-frame sensor=全画幅传感器|Aperture blades=光圈叶片散景|Shutter speed=快门速度运动模糊|ISO Sensitivity=原生双基准感光度|Depth of field=景深虚化范围|Focal length=等效焦距视角|Prime lens=大光圈定焦镜头|Anamorphic lens=变形宽银幕镜头|Chromatic aberration=色散紫边抑制|Vignetting=边缘暗角控制|Lens flare=逆光眩光鬼影|Rolling shutter=果冻效应(卷帘快门)|Global shutter=全局快门传感器|IBIS=机身五轴防抖|Autofocus tracking=眼部实时追焦|ND Filter=中灰减光镜|CPL Polarizer=偏振镜消除反光|Gimbal stabilizer=三轴电子稳定器|Time-lapse=间隔延时摄影|Zebra stripes=斑马纹过曝提示|Focus peaking=峰值对焦高亮|ProRes RAW=无损原始视频流|CFexpress Card=高速存储卡写入|Color temperature=色温白平衡(K)"],
  [29, 4, "动漫制作与分镜", "Storyboard=分镜脚本手稿|Key animation=原画关键张|In-between=中间画补间动画|Layout (LO)=画面构图与景深设计|Timesheet (X-Sheet)=律表时间摄影表|Animatic=动态分镜预演|Cel shading=三渲二卡通渲染|Sakuga=作画爆发片段|Frame rate (12fps)=一拍二/一拍三帧率|Smear frame=运动残影变形帧|Impact frames=黑白闪冲击帧|Camera pan=横移摇镜运镜|Multiplane camera=多层景深视差镜头|Character sheet=角色三视图设定集|Color script=全片色彩情绪脚本|Compositing=摄影后期合成特效|Chromatic blur=二值化色散摄影|Lip sync=口型配音对轨|Seiyuu dubbing=声优后期配音|OP / ED Credit=片头片尾演职员表|Blu-ray correction=BD蓝光作画修正|Pencil test=线拍动作测试|Background art=美术背景绘制|Cel layer=赛璐珞分层图层"],

  [30, 5, "航空航天工程", "Aerodynamics=空气动力学|Wind tunnel=风洞吹风试验|Mach number=马赫数超音速|Boundary layer=附面层分离|Angle of attack=迎角(攻角)失速|Lift-to-drag ratio=升阻比优化|Turbofan engine=大涵道比涡扇发动机|Afterburner=加力燃烧室|Thrust vectoring=推力矢量喷管|Avionics=综合航空电子系统|Fly-by-wire=电传操纵系统|Pitot tube=空速管动压测量|Inertial navigation=激光陀螺惯导|Carbon composite=碳纤维复材机身|Liquid oxygen=液氧甲烷推进剂|Raptor engine=全流量分级燃烧循环|Specific impulse=比冲(Isp)效率|Gimbal thrust=万向节摆动发动机|Max-Q=最大动压点|Fairing separation=整流罩分离|Orbital insertion=轨道入轨机动|Reaction control=姿控发动机(RCS)|Heat shield=可重复防热瓦|Telemetry=遥测数据下传"],
  [31, 5, "生物医药与基因", "CRISPR-Cas9=基因编辑技术|mRNA Vaccine=信使核糖核酸疫苗|Genome sequencing=高通量基因测序|PCR Amplification=聚合酶链式反应|Monoclonal antibody=单克隆抗体靶向药|Protein folding=蛋白质三维折叠|AlphaFold=蛋白质结构预测|Clinical trial=三期临床双盲试验|Pharmacokinetics=药物代谢动力学|Bioavailability=生物利用度|Blood-brain barrier=血脑屏障穿透|Stem cell=诱导多能干细胞|CAR-T Therapy=嵌合抗原受体T细胞|Bioreactor=细胞发酵生物反应器|Centrifuge=超速冷冻离心机|Chromatography=液相色谱纯化|Mass spectrometry=质谱分析鉴定|Pathogen=病原体微生物|Antigen / Antibody=抗原与中和抗体|Cytokine storm=细胞因子风暴|Half-life=药物半衰期|Adverse event=不良反应事件|Placebo effect=安慰剂对照效应|FDA Approval=新药上市审批"],
  [32, 5, "量化金融与交易", "High-frequency trading=高频算法交易|Order book=限价订单簿深度|Bid-ask spread=买卖盘口价差|Slippage=交易滑点损耗|Sharpe ratio=夏普比率风险收益|Maximum drawdown=最大回撤控制|Alpha factor=超额收益阿尔法因子|Beta exposure=市场贝塔风险敞口|Statistical arbitrage=统计套利策略|Market making=做市商双边报价|Mean reversion=均值回归模型|Momentum strategy=动量趋势追踪|Backtesting=历史行情回测|Over-optimization=参数过拟合陷阱|Volatility surface=隐含波动率曲面|Black-Scholes=期权定价模型|Delta hedging=Delta动态对冲|Liquidity pool=流动性资金池|Smart contract=链上智能合约|Zero-knowledge proof=零知识证明(ZKP)|Consensus mechanism=分布式共识机制|Latency arbitrage=微秒级延迟套利|Stop-loss trigger=止损平仓线|Monte Carlo sim=蒙特卡洛随机模拟"],
  [33, 5, "建筑BIM与土木", "BIM Modeling=建筑信息模型|Revit Family=参数化族库组件|Clash detection=管线综合碰撞检查|MEP Engineering=机电暖通给排水|Point cloud scan=三维激光扫描逆向|Reinforced concrete=钢筋混凝土结构|Prestressed tendon=预应力钢绞线|Shear wall=抗震剪力墙|Pile foundation=深基坑桩基础|Cantilever beam=悬挑梁结构|Truss structure=大跨度空间钢桁架|Curtain wall=玻璃幕墙单元|Seismic isolation=隔震橡胶支座|Structural load=静载与风荷载计算|Finite element=结构有限元分析|Total station=全站仪坐标放样|Geotechnical=岩土工程勘察|Shield tunneling=盾构机掘进管片|Formwork=铝合金模板支设|Curing period=混凝土养护周期|Thermal bridge=建筑热桥保温|Green building=超低能耗绿色建筑|Digital twin=智慧工地数字孪生|Quantity takeoff=工程量自动算量"],
  [34, 5, "电气自动化与电网", "Smart grid=智能电网调度|UHV Transmission=特高压输电线路|Substation=变电站GIS开关|Power transformer=油浸式主变压器|Circuit breaker=真空断路器跳闸|Relay protection=继电保护整定|Harmonic filter=有源电力滤波器|Reactive power=无功功率补偿(SVG)|Power factor=功率因数校正|Three-phase balance=三相负载平衡|SCADA EMS=能量管理系统|Photovoltaic inverter=光伏组串式逆变器|MPPT Tracking=最大功率点跟踪|Wind turbine=双馈风力发电机|Energy storage=电网级储能电站|Microgrid=风光储微电网|Islanding effect=孤岛效应保护|Arc fault=电弧故障断路器|Grounding resistance=防雷接地电阻|Insulation tester=兆欧表绝缘测试|Busbar differential=母线差动保护|Load shedding=低频减载策略|Peak shaving=削峰填谷电价|Partial discharge=局部放电在线监测"],
  [35, 5, "极客数码与硬件评测", "Cinebench R23=多核渲染跑分|3DMark TimeSpy=图形理论性能测试|1% Low FPS=1%最低帧流畅度|Frame time graph=帧生成时间曲线|Power consumption=整机墙插功耗|Thermal imaging=热成像表面温度|Heatpipe cooler=均热板与热管散热|Liquid metal=液态金属导热|Mechanical switch=客制化机械轴体|Gasket mount=Gasket垫片软弹结构|PBT Keycaps=热升华PBT键帽|Polling rate=8KHz无线回报率|OLED Burn-in=OLED烧屏防残影|PWM Dimming=高频PWM护眼调光|Peak brightness=HDR峰值激发亮度|Delta-E=色准偏离度平均值|Refresh rate=可变刷新率(VRR)|Thunderbolt 5=雷电5高速接口|Wi-Fi 7 MLO=多链路聚合传输|NAS Storage=网络附属存储阵列|RAID 5 Array=磁盘冗余阵列|UPS Power=不间断后备电源|Teardown=完全拆解内部做工|Repairability=可维修性评分"]
];

/* 解析成 { id, sector, name, items: [{src, zh, active}] } */
const GLOSSARY_36 = IND_RAW.map(row => ({
  id: row[0],
  sector: row[1],
  name: row[2],
  items: row[3].split('|').map(pair => {
    const i = pair.indexOf('=');
    return { src: pair.slice(0, i), zh: pair.slice(i + 1), active: true };
  })
}));