3b1f
3af3
A Lightweight Model-Driven 4D Radar Framework
for Pervasive Human Detection in Harsh Conditions
Zhenan Liu
Mechanical & Mechatronics Engineering
University of Waterloo
Waterloo, Canada
z634liu@uwaterloo.caAmir Khajepour
Mechanical & Mechatronics Engineering
University of Waterloo
Waterloo, Canada
a.khajepour@uwaterloo.caGeorge Shaker
Electrical & Computer Engineering
University of Waterloo
Waterloo, Canada
gshaker@uwaterloo.ca
Abstract—Pervasive sensing in industrial and underground
environments is severely constrained by airborne dust, smoke,
confined geometry, and metallic structures, which rapidly de-
grade optical and LiDAR-based perception. Elevation-resolved
4D millimeter-wave (mmWave) radar offers strong resilience
to such conditions, yet there remains a limited understanding
of how to process its sparse and anisotropic point clouds for
reliable human detection in enclosed, visibility-degraded spaces.
This paper presents a fully model-driven 4D radar perception
framework designed for real-time execution on embedded edge
hardware. The system uses radar as its sole perception modal-
ity and integrates domain-aware multi-threshold filtering, ego-
motion–compensated temporal accumulation, KD-tree Euclidean
clustering with Doppler-aware refinement, and a rule-based 3D
classifier. The framework is evaluated in a dust-filled enclosed
trailer and in real underground mining tunnels, and in the tested
scenarios the radar-based detector maintains stable pedestrian
identification as camera and LiDAR modalities fail under severe
visibility degradation. These results suggest that the proposed
model-driven approach provides robust, interpretable, and com-
putationally efficient perception for safety-critical applications in
harsh industrial and subterranean environments.
Index Terms—4D mmWave radar, human detection, harsh
environments, industrial sensing, model-driven perception, per-
vasive safety systems, radar point clouds, underground mining.
I. INTRODUCTION
Pervasive sensing in industrial, construction, and under-
ground environments is fundamentally constrained by visi-
bility. Airborne dust, smoke, confined geometry, and metal-
lic infrastructure often cause optical sensors and LiDAR to
fail, resulting in missed detections and unreliable situational
awareness. Millimeter-wave (mmWave) radar, in contrast, is
inherently resilient to illumination changes and particulate
interference, making it a promising modality for such harsh
environments.
Conventional automotive radars, however, provide only
coarse angular resolution and typically lack elevation esti-
mation, yielding sparse range–azimuth–Doppler measurements
that are difficult to interpret in enclosed metallic spaces
where multipath and ghost reflections are prevalent. Recent
advances in4D imaging radarintroduce elevation estimation
through large virtual MIMO arrays, producing 3D point clouds
enriched with Doppler and radar cross section (RCS). Al-
though these sensors have shown promise in outdoor visibility-
Fig. 1: Overview of the model-driven 4D radar pipeline: raw radar measure-
ments through multi-threshold filtering, ego-motion–compensated temporal
accumulation, clustering, Doppler-aware refinement, rule-based 3D classifi-
cation
degraded settings, there is no established consensus on how to
process their anisotropic, noise-prone point clouds in confined
industrial or underground environments dominated by dust and
reflective surfaces.
From this context, three specific gaps emerge:
1)Sparse evaluation of 4D radar in enclosed envi-
ronments with visibility degradation.Existing studies
focus primarily on outdoor automotive scenes. Radar
behaviour in dust-filled, metal-walled spaces, such as
industrial corridors or underground tunnels, remains
largely uncharacterized.
2)Limited development of radar-specific point-cloud
processing pipelines.Compared with extensive research
on optical and LiDAR perception in adverse weather, far
fewer works investigate radar-specific filtering, Doppler
conditioning, and cluster-level reasoning tailored to 4D
radar noise characteristics.
3)Need for strong, interpretable model-driven base-
lines.The rapid emergence of learning-based radar de-
tectors underscores the need for lightweight, physics-
motivated baselines that operate reliably in harsh en-
closed environments and provide stable targets for future
learning-based extensions.
To address these gaps, this paper proposes afully model-arXiv:2601.13373v1  [cs.CV]  19 Jan 2026driven 4D radar perception frameworkfor real-time human
detection in industrial and underground scenarios. The system
uses 4D radar as itsonly perception modality, while relying on
external LiDAR–IMU odometry solely for ego-motion com-
pensation and on LiDAR/camera data only for visualization
and baseline comparison.
The main contributions of this work are:
•A radar-only, model-driven 4D perception pipeline
tailored for enclosed dusty and metallic environments,
filling a gap left by prior work focused on outdoor
driving, sensor fusion, or learning-based detectors.
•A domain-aware multi-threshold filtering moduleus-
ing RCS bounds, angular constraints, and Doppler plau-
sibility to suppress multipath and clutter common in
confined spaces.
•A lightweight two-frame temporal accumulation
schemethat uses external LiDAR–IMU odometry solely
for ego-motion compensation, improving spatial consis-
tency while keeping the system real-time and embedded-
friendly.
•A Doppler-aware clustering and deterministic 3D
classifierthat infers object type, motion state, and line-
of-sight movement without training.
•Experimental validation in controlled dust and under-
ground tunnels, demonstrating—in our tested scenar-
ios—that 4D radar maintains stable pedestrian detection
where camera and LiDAR sensing are severely degraded.
II. RELATEDWORK
A. Perception in Adverse Environments
Optical and LiDAR-based perception degrades sharply un-
der adverse visibility conditions such as rain, fog, airborne
dust, and low light. Numerous studies report substantial losses
in detection and tracking performance even with advanced
enhancement models [1], [2]. LiDAR suffers from attenu-
ation, backscatter, and spurious returns that reduce point-
cloud completeness [3]. These limitations intensify in confined
or underground spaces where metallic infrastructure creates
strong multipath and where maintaining situational awareness
is intrinsically challenging [4]. These trends motivate sensing
modalities that remain reliable when optical pathways col-
lapse.
B. Conventional Automotive Radar
Automotive mmWave radars (76–81 GHz) provide robust
range and Doppler estimates and are widely deployed for
highway safety [5], [6]. However, limited antenna aperture
yields coarse angular resolution and no direct elevation es-
timate. Standard radar outputs provide sparse range–azimuth–
Doppler detections rather than full 3D structure, and their
performance deteriorates in enclosed metallic environments
due to multipath, ghost reflections, and anisotropic lobes [7].
These constraints limit the applicability of conventional radar
to object-level perception in industrial or underground spaces.C. 4D Imaging Radar and Existing Datasets
Recent 4D mmWave imaging radars provide elevation
estimation through large virtual MIMO arrays, generating
anisotropic 3D point clouds enriched with Doppler and
RCS [8]. This capability has inspired several learning-based
perception studies and datasets. TJ4DRadSet [9] and View-of-
Delft (V oD) [10] provide radar, camera, and LiDAR data for
outdoor autonomous driving, including rain and artificial fog
scenarios. Other works examine radar–camera fusion or deep
networks for pedestrian detection [11].
However, these datasets and methods share two key limi-
tations. First, their adverse-weather scenes are dominated by
water-based fog or smoke and occur in wide outdoor spaces.
They do not capture the unique behavior of mmWave radar
in confined, dust-filled, metallic environments where cavity
propagation, strong multipath, and sparse returns differ funda-
mentally from outdoor driving settings. Second, most percep-
tion pipelines rely on learning-based models or radar–camera
fusion, which require large labeled datasets and are difficult
to deploy on lightweight embedded hardware.
D. Classical Geometric Pipelines
Traditional geometric pipelines rely on nearest-neighbour
search, clustering, and principal-component analysis for
bounding-box estimation [12]. Euclidean clustering with KD-
tree acceleration is widely used for LiDAR processing [13],
but such methods assume relatively dense and isotropic point
clouds. In contrast, 4D radar returns are sparse and anisotropic,
and their Doppler information is rarely exploited in classical
frameworks.
E. Novelty and Positioning of This Work
Compared with prior research, this work differs in three
fundamental ways:
1)Environment:Prior 4D radar studies focus on outdoor
autonomous driving, whereas this work targets enclosed,
dust-filled, metallic environments representative of in-
dustrial and underground operations.
2)Modality and independence from vision:Unlike
radar–camera fusion or deep-learning detectors that re-
quire labeled datasets, our framework uses radar as
thesole perception modality, relying only on external
odometry for motion compensation.
3)Model-driven, lightweight design:Existing methods
often employ heavy learning pipelines. We introduce a
fully model-driven radar pipeline that integrates phys-
ically interpretable filtering, Doppler-aware clustering,
and rule-based 3D classification for real-time perfor-
mance on embedded hardware.
To our knowledge, this is the first work to demonstrate
a fully model-driven, radar-only 4D perception framework
operating in enclosed, dust- and metal-filled industrial environ-
ments, without any reliance on optical modalities or supervised
learning.III. METHODOLOGY
A. 4D Radar Platform and Multi-Sensor Setup
The 4D mmWave imaging radar used in this work is a
cascaded four-chip device equipped with 12 transmit (TX)
and 16 receive (RX) antennas arranged into a large virtual
MIMO array capable of resolving range, azimuth, elevation,
and Doppler [14]. Manufacturer specifications list azimuth and
elevation resolutions on the order of1–2◦and pedestrian detec-
tion ranges of approximately 200 m; these values serve only
as context, as all analysis relies on empirical measurements
collected in our experiments.
Radar data are acquired using the vendor’s ROS driver,
which receives UDP packets, decodes the raw range–angle–
Doppler detections, converts them into Cartesian coordinates,
and publishes the resulting elevation-resolved radar point
cloud assensor_msgs/PointCloud2at 15 Hz. Logging
and timestamping are performed on an NVIDIA Jetson Orin
NX edge computer. Extrinsic calibration between radar, Li-
DAR, and the IMU is maintained within the ROStftree
(radar→base_link→world), ensuring consistent geo-
metric registration across all sensors.
The radar is rigidly co-mounted with a 40-line LiDAR and
an infrared (IR) camera. LiDAR provides geometric reference
and supplies LiDAR–IMU odometry used exclusively for ego-
motion compensation; it does not participate in perception or
decision-making. The IR camera documents visibility degra-
dation and serves only as a qualitative baseline. All perception
outputs in this work are derived solely from the 4D radar point
clouds.
Each radar point is represented as
pi= (x i, yi, zi, vdop
i,RCS i, δi),(1)
where(x i, yi, zi)are Cartesian coordinates,vdop
i is radial
Doppler velocity,RCS iis radar cross section, andδ iis
a vendor-supplied dynamic/static flag. Indoor radar frames
typically contain 3 000–6 000 points.
B. Domain-Aware Multi-Threshold Filtering
The first processing stage removes spurious radar points
arising from diffuse reflections, metallic multipath, and im-
plausible Doppler measurements. Two threshold profiles are
used:
Indoor profile:strict angular and RCS limits to suppress
wall- and ceiling-induced multipath in enclosed spaces (e.g.,
azimuth[−5,5]◦, elevation[−2,8]◦, RCS[0,45]).
Outdoor profile:wider angular and RCS bounds to ac-
commodate long-range reflections and broader operational
coverage (e.g., azimuth[−15,15]◦, elevation[−6,12]◦, RCS
[−5,55]).
Each raw radar pointp iis subjected to three filters.
a) 1) RCS thresholding.:Very weak diffuse returns and
extremely strong multipath-induced peaks are rejected:
RCS min<RCS i<RCS max.(2)b) 2) Angular bound filtering.:Azimuthθ iand elevation
ϕimust lie within the radar’s valid field of view:
θi∈[θ min, θmax], ϕ i∈[ϕ min, ϕmax].(3)
Points outside these bounds are frequently associated with
ceiling/floor multipath or grazing-angle reflections.
c) 3) Doppler plausibility filtering.:Points with Doppler
velocities inconsistent with human or machinery motion, or
zero-Doppler points in locations not physically associated with
static structures, are removed:
vmin≤vdop
i≤vmax.(4)
The resulting filtered set is
Pfiltered ={p i∈ Praw|RCS min<RCS i<RCS max,
θi∈[θ min, θmax], ϕ i∈[ϕ min, ϕmax],
vdop
i∈[v min, vmax]},(5)
executed inO(n)time per frame. This significantly reduces
downstream fragmentation during clustering.
C. Temporal Accumulation with Ego-Motion Compensation
Radar point clouds are sparser than LiDAR and may contain
only a few points per pedestrian in each frame. To improve
spatial continuity while maintaining real-time performance, we
accumulateexactly twoconsecutive frames.
Odometry is provided by FAST-LIO [15], a tightly cou-
pled LiDAR–IMU fusion system that outputs six-degree-of-
freedom poses at high frequency on a ROS/odomtopic.
These poses are used solely to (i) compensate for ego-motion
when aligning radar frames and (ii) compute the platform
velocityv egoused in Doppler correction. Radar perception
itself remains radar-only.
LetP tandP t−1 denote the filtered radar point clouds
at consecutive timestamps. The transformTt
t−1∈SE(3)
maps points from the(t−1)frame to thetframe. Because
odometry updates run faster than radar frames, poses are time-
synchronized and interpolated to radar timestamps.
The previous cloud is transformed as:
Pt
t−1={Tt
t−1pi|pi∈ Pt−1},(6)
and the accumulated cloud becomes:
Pacc=Pt∪ Pt
t−1.(7)
The two-frame window improves spatial consistency with-
out noticeable latency and without accumulating drift. Because
the radar does not strongly sense the ground, no ground-plane
removal or voxel downsampling is required.
D. KD-Tree Euclidean Clustering and Doppler-Aware Filter-
ing
LetP acc={p i}N
i=1. A KD-tree is constructed over the
spatial coordinates(x i, yi, zi)to enable neighbour queries in
O(logN)time.
0
x-amz-checksum-crc32:pRrkSg==


0
x-amz-checksum-crc32:EiVcXQ==

