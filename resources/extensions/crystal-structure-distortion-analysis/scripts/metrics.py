"""One package-owned dictionary for readable labels, definitions and formulas."""
ROWS=[
('mean_bond_length_angstrom','平均键长 d̄','Å','d̄ = Σdᵢ / n',r'\bar d=\frac{1}{n}\sum_i d_i','所选中心到各配体的距离平均值。','长度尺度，不是畸变判据'),
('bond_length_std_angstrom','键长标准差 σd','Å','σd = √[Σ(dᵢ−d̄)² / n]',r'\sigma_d=\sqrt{\frac{1}{n}\sum_i(d_i-\bar d)^2}','采用总体标准差，描述各条键的离散程度。','等长时为 0'),
('bond_length_cv','键长变异系数 CV','无量纲','CV = σd / d̄',r'CV=\frac{\sigma_d}{\bar d}','按平均键长归一化的标准差。','等长时为 0'),
('bond_length_range_angstrom','最长与最短键之差','Å','极差 = max(dᵢ) − min(dᵢ)',r'\Delta d_{range}=\max_i d_i-\min_i d_i','只反映两个极值，不代表完整分布。','等长时为 0'),
('bond_length_distortion_delta','键长畸变 Δd','无量纲','Δd = Σ[(dᵢ−d̄)/d̄]² / n',r'\Delta_d=\frac{1}{n}\sum_i\left(\frac{d_i-\bar d}{\bar d}\right)^2','相对键长偏差平方的平均值；与 CV² 一致。','等长时为 0'),
('baur_distortion_index','Baur 畸变指数 D','无量纲','D = Σ|dᵢ−d̄| / (n d̄)',r'D=\frac{1}{n}\sum_i\frac{|d_i-\bar d|}{\bar d}','相对偏差绝对值的平均值，与平方偏差 Δd 不同。','等长时为 0'),
('polyhedron_volume_angstrom3','配体凸包体积 V','Å³','V = 配体顶点凸包体积',r'V=V_{hull}','由所选配体顶点构成的凸包体积，不是晶胞体积。','表示大小，不单独代表畸变'),
('equal_volume_ideal_bond_length_angstrom','等体积理想键长 d₀','Å','八面体 d₀=(3V/4)^(1/3)；四面体 d₀=(9√3V/8)^(1/3)',r'd_0^{oct}=(3V/4)^{1/3},\quad d_0^{tet}=(9\sqrt{3}V/8)^{1/3}','与当前凸包等体积的规则多面体中心到顶点的距离。','用于二次伸长率的参考'),
('quadratic_elongation','二次伸长率 λ','无量纲','λ = Σ(dᵢ/d₀)² / n',r'\lambda=\frac{1}{n}\sum_i\left(\frac{d_i}{d_0}\right)^2','参考等体积规则多面体；d₀ 不是当前平均键长。','规则多面体为 1'),
('mean_angle_degree','平均内部参考角','°','θ̄ = Σθᵢ / m',r'\bar\theta=\frac{1}{m}\sum_i\theta_i','四面体包含全部 6 角；八面体此项只包含 12 个顺式角。','四面体约 109.471221°；八面体 90°'),
('angle_variance_degree2','键角方差 σ²θ','°²','σ²θ = Σ(θᵢ−θ₀)² / (m−1)',r'\sigma_\theta^2=\frac{\sum_i(\theta_i-\theta_0)^2}{m-1}','四面体 m=6、θ₀=arccos(−1/3)；八面体 m=12、θ₀=90°，不混入对向角。','理想时为 0；不是围绕实测均值的方差'),
('angle_rms_deviation_degree','键角均方根偏差','°','RMSθ = √[Σ(θᵢ−θ₀)² / m]',r'RMS_\theta=\sqrt{\frac{1}{m}\sum_i(\theta_i-\theta_0)^2}','使用与角方差相同的角集合，但分母为 m，最后开方。','理想时为 0'),
('angle_mean_abs_deviation_degree','键角平均绝对偏差','°','MADθ = Σ|θᵢ−θ₀| / m',r'MAD_\theta=\frac{1}{m}\sum_i|\theta_i-\theta_0|','使用同一参考角，对偏差取绝对值后平均。','理想时为 0'),
('q2_angstrom','对向键差分 Q₂','Å','Q₂ = √2(l₁−l₂)',r'Q_2=\sqrt{2}(l_1-l_2)','三组对向键的平均长度按 l₁≤l₂≤l₃ 排序。编号不是晶体学 x/y/z。','等长时为 0；符号不代表轨道方向'),
('q3_angstrom','对向键伸缩 Q₃','Å','Q₃ = √(2/3)(2l₃−l₁−l₂)',r'Q_3=\sqrt{\frac{2}{3}}(2l_3-l_1-l_2)','使用按长度排序的三组对向键平均值，不定义真实局域轴。','等长时为 0'),
('q_jt_angstrom','几何畸变幅度 QJT','Å','QJT = √(Q₂²+Q₃²)',r'Q_{JT}=\sqrt{Q_2^2+Q_3^2}','八面体对向键长度不均匀的几何幅度；不直接证明电子态 Jahn–Teller 效应。','三组对向键均值相等时为 0'),
('mean_trans_angle_degree','平均对向角','°','θ̄trans = Σφᵢ / 3',r'\bar\phi=\frac{1}{3}\sum_i\phi_i','八面体配对按三个对向角之和最大确定，不用任意固定原子顺序。','理想时为 180°'),
('trans_angle_variance_degree2','对向角偏差方差','°²','σ²trans = Σ(φᵢ−180°)² / 2',r'\sigma_{trans}^2=\frac{\sum_i(\phi_i-180^\circ)^2}{2}','三组对向角围绕 180° 的平方偏差和，分母为 3−1。','理想时为 0'),
('trans_angle_rms_deviation_degree','对向角均方根偏差','°','RMStrans = √[Σ(φᵢ−180°)² / 3]',r'RMS_{trans}=\sqrt{\frac{1}{3}\sum_i(\phi_i-180^\circ)^2}','三组对向角偏差的均方根。','理想时为 0'),
('coordination_shell_gap_angstrom','配位壳层间隔','Å','间隔 = dₙ₊₁ − dₙ',r'g=d_{n+1}-d_n','第 n 与 n+1 个配体候选的距离差；搜索半径内没有下一个候选时为未知，不代表无穷大。','小于 0.15 Å 提示检查配位模型'),
]
METRICS={key:dict(key=key,label=label,unit=unit,formula=formula,tex=tex,meaning=meaning,reference=reference) for key,label,unit,formula,tex,meaning,reference in ROWS}
