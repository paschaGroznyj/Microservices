4b8c
4b60
1
SMc2f: Robust Scenario Mining for Robotic
Autonomy from Coarse to Fine
Yifei Chen,Graduate Student Member, IEEE,and Ross Greer,Member, IEEE
Abstract—The safety validation of autonomous robotic vehicles
hinges on systematically testing their planning and control stacks
against rare, safety-critical scenarios. Mining these ‘long-tail’
events from massive real-world driving logs is therefore a critical
step in the robotic development lifecycle. The goal of the Scenario
Mining task is to retrieve useful information to enable targeted re-
simulation, regression testing, and failure analysis of the robot’s
decision-making algorithms. RefA V , introduced by the Argoverse
team, is an end-to-end framework that uses large language
models (LLMs) to spatially and temporally localize scenarios
described in natural language. However, this process performs
retrieval on trajectory labels, ignoring the direct connection
between natural language and raw RGB images, which runs
counter to the intuition of video retrieval; it also depends on the
quality of upstream 3D object detection and tracking. Further, in-
accuracies in trajectory data lead to inaccuracies in downstream
spatial and temporal localization. To address these issues, we
propose Robust Scenario Mining for Robotic Autonomy from
Coarse to Fine (SMc2f), a coarse-to-fine pipeline that: employs
vision–language models (VLMs) for coarse image–text filtering,
builds a database of successful mining cases on top of RefA V
and automatically retrieves exemplars to few-shot condition the
LLM for more robust retrieval, and introduces text–trajectory
contrastive learning to pull matched pairs together and push
mismatched pairs apart in a shared embedding space, yielding
a fine-grained matcher that refines the LLM’s candidate trajec-
tories. Experiments on public datasets demonstrate substantial
gains in both retrieval quality and efficiency. We make our code
available at:https://anonymous.4open.science/r/test-EE20
I. INTRODUCTION
The development and validation of autonomous vehicles
(A Vs) are inherently data-driven endeavors [1], [2]. Mod-
ern A V fleets, equipped with a comprehensive suite of
sensors—including LiDAR, radar, and high-resolution cam-
eras—generate unprecedented volumes of multi-modal data.
A single vehicle can generate terabytes of data per hour
during operation. This capacity for large-scale data acquisition
presents both an opportunity and a challenge: transforming
vast, uncurated driving logs into structured and actionable
knowledge is essential for advancing A V safety and relia-
bility. Such raw data is indispensable for training perception
models, validating planning and control algorithms, and, most
critically, identifying and understanding rare or safety-critical
events for targeted system improvement.
A central challenge in this process is the well-documented
long-tail problem [3]. The distribution of driving scenarios is
highly imbalanced: the majority of collected data corresponds
to routine, repetitive events such as highway cruising or
waiting at a traffic light. In contrast, complex, unpredictable,
Y . Chen is with the Department of Computer Science at Xi’an University
of Technology. R. Greer is with the department of Computer Science &
Engineering at the University of California, Merced.and safety-critical scenarios—such as pedestrians unexpect-
edly crossing against signals, aggressive cut-ins, or interactions
with emergency vehicles—are exceedingly rare. Despite their
low frequency, these long-tail events are precisely the situa-
tions that stress the operational design domain of A Vs and are
crucial for constructing a comprehensive safety case. However,
manually sifting through petabytes of data to extract such rare
scenarios is prohibitively time-consuming and costly, making
scalable and automated scenario mining a necessity for the A V
industry.
Traditional scenario mining approaches [4], [5] typically
rely on structured query languages or hand-crafted heuristics to
define events of interest. While effective for simple and well-
defined events (e.g., identifying left turns at intersections),
these methods lack the expressiveness required to capture the
nuanced, compositional, and often subjective characteristics
of complex real-world interactions. For instance, a query such
as “a truck cuts off a car, causing the car to brake abruptly”
involves reasoning about causality, temporal ordering, and sub-
jective interpretation—concepts that are difficult to articulate
in rigid query-based frameworks.
To address these limitations, the field is rapidly shifting
toward natural language–based scenario mining [6]. This
paradigm enables engineers, safety analysts, and developers
to describe complex driving situations through intuitive, free-
form text. The leading academic benchmark in this area is
the Argoverse 2 Scenario Mining Challenge [7], which builds
upon the comprehensive Argoverse 2 sensor dataset [7]. The
challenge includes 1,000 driving logs, each 15 seconds long,
collected across six U.S. cities. It features 10,000 diverse,
planning-centric natural language queries and requires par-
ticipating systems to perform a multi-level localization task:
(i) determine whether the queried scenario occurs within a
log, (ii) temporally localize its start and end times, and (iii)
spatially identify and track all relevant actors involved.
The baseline method for this challenge, RefA V [8], lever-
ages an LLM as a program synthesizer. Specifically, RefA V
translates a natural language query into an executable Python
program composed of a predefined library of atomic functions.
These functions encapsulate fundamental state checks (e.g.,
is turning()) and relational predicates (e.g., has objects in
front()) that operate directly on 3D object tracks. By chaining
these functions, the LLM constructs complex logical programs
capable of filtering and retrieving trajectory segments that
match the semantics of the natural language description.
Despite its contributions, the RefA V framework exhibits
several fundamental limitations that hinder both robustness and
scalability. One major challenge lies in its reliance on zero-
shot code generation by LLMs. Without explicit supervisionarXiv:2601.12010v1  [cs.CV]  17 Jan 20262
or task-specific adaptation, LLMs often produce code with
syntactic errors, logical inconsistencies, or incorrect parame-
terizations, which can result in runtime failures that terminate
the mining process prematurely. The RefA V inference process
operates solely on the abstract outputs of an upstream 3D
perception and tracking system, without access to the rich
semantic cues embedded in RGB images. Finally, the RefA V
pipeline suffers from computational inefficiency. For each
query, the LLM-generated program must be executed over
the full set of object tracks in a log. This process becomes
increasingly time-consuming for complex queries that involve
numerous relational checks. These limitations motivate us to
develop an alternative approach that establishes a more robust
and direct connection between language and visual evidence.
To address these issues, we present Robust Scenario Mining
for Robotic Autonomy from Coarse to Fine, a principled multi-
stage framework for robust and efficient scenario mining. The
design of the framework follows a coarse-to-fine paradigm
that progressively narrows the search space while increasing
semantic precision.
The main contributions of our research are as follows:
•We employ vision–language models (VLMs) for an ini-
tial coarse filtering stage that operates directly on raw
RGB video. Leveraging CLIP’s cross-modal alignment,
we rapidly identify query-consistent temporal segments,
pruning the search space and focusing later stages on the
most relevant portions of the log.
•We propose a knowledge-base-guided few-shot method
to improve the robustness of code generation. We build a
knowledge base of successful description–trajectory–code
triplets and retrieve relevant examples for new queries.
Integrated into few-shot prompts, these examples provide
in-context guidance that enhances code reliability while
reducing hallucinations and runtime errors.
•We conduct fine-grained text–trajectory matching with
a Dual-Encoder Text–Trajectory Matcher (DETTM).
Trained contrastively, DETTM embeds language and
spatio-temporal trajectories in a shared space, enabling
precise re-ranking of candidate tracks and reducing false
positives.
II. RELATEDWORK
A. Video retrieval
Text–video retrieval is a task closely related to scenario
mining: both aim to locate semantically coherent segments
within large-scale, long-horizon data streams based on natural
language descriptions. Within the video-retrieval literature,
multimodal fusion has emerged as the dominant paradigm,
offering useful insights for scenario mining.
Since 2020, progress in text–video retrieval has been driven
by advances in cross-modal alignment and temporal mod-
eling. In 2020, Gabeur et al. [9] introduced a multimodal
Transformer that jointly encodes visual modalities and models
temporal dependencies via cross-modal attention, optimizing
language and video features together. In 2021, Wang et al. [10]
proposed T2VLAD, which employs shared semantic centers
to achieve efficient global–local alignment for fine-grained
Ego vehicle following 
vehicle  being crossed by 
a jaywalking pedestrianLLMs  Executable Python scriptAll Tracks Mined Tracks
Scenario Mining
Ego vehicle following 
vehicle  being crossed by 
a jaywalking pedestrianLLMs  Executable Python scriptAll Tracks Mined Tracks
Scenario MiningFig. 1. Overview of the RefA V baseline framework. A natural language
query is processed by a Large Language Model (LLM) to synthesize an
executable Python script. This script then filters the full set of trajectory data
(“All Tracks”) to retrieve specific segments that match the scenario description
(“Mined Tracks”).
comparisons. In 2022, Gorti et al. [11] presented X-Pool,
enabling selective attention to semantically relevant frames and
effectively filtering visual noise. In 2023, Wu et al. [12] devel-
oped Cap4Video, which leverages zero-shot video-generated
captions for data augmentation, cross-modal interaction, and
auxiliary inference, achieving strong benchmark results. In
2024, Wang et al. [13] introduced T-MASS, a stochastic text-
embedding strategy that treats queries as deformable seman-
tic masses, incorporating similarity-aware radii and support-
text regularization to improve expressiveness, setting new
records across five datasets. Entering 2025, Zhang et al. [14]
proposed TokenBinder, a two-stage framework with coarse-
to-fine one-to-many alignment and a Focused-view Fusion
Network for cross-attention, attaining state-of-the-art results
across six benchmarks, while Bian et al. [15] released the SMA
framework, which performs selective multi-grained alignment
at both video–sentence and object–phrase levels using token
aggregation and similarity-aware keyframe selection, yielding
strong performance on MSR-VTT, ActivityNet, and beyond.
The motivation behind these advances closely parallels that
of scenario mining: both tasks require locating a contiguous
scene that corresponds to a natural-language description. How-
ever, scenario mining often demands finer-grained retrieval
focused on the trajectory of a specific agent or a set of
interacting agents. Whereas most video-retrieval approaches
rely on direct alignment between visual and textual semantics,
this presents an opportunity for scenario mining: cross-modal
semantic matching can serve as a coarse filtering stage, after
which detailed analysis can be restricted to the subset of
trajectories identified.
B. Scenario mining
Early methods in scenario mining focused on rule-based
systems and human-defined criteria. These methods encode
traffic laws, domain expertise, and parameters from acci-
dent databases into formal languages and ontologies [16].
By defining a logical scenario space with parameters (e.g.,
road curvature, number of vehicles, weather) and their valid
ranges, scenarios can be generated through techniques like
combinatorial testing to cover a wide array of predefined
conditions. A significant initiative in this area is the PEGASUS
project [17], which established a systematic, knowledge-driven3
workflow for defining scenarios. Formal languages such as
ASAM OpenSCENARIO [18] have become industry standards
for describing the dynamic content of driving scenarios. The
primary advantage of knowledge-based methods is the high
degree of control and interpretability, making them ideal for
testing system compliance with known rules. However, their
main limitation is that they are bound by existing knowledge
and manual effort, often failing to uncover novel modes and
lacking the behavioral complexity of real-world traffic.
With the advent of large-scale, real-world driving datasets,
data-driven methods have become prominent. These ap-
proaches mine sensor logs to extract realistic scenarios or
learn generative models of traffic behavior. A typical pipeline
acquires data from the Waymo Open Motion Dataset [19],
nuScenes [20], or Argoverse 2 [21], identifies high-criticality
events, and—via SQL, a bespoke DSL, or a general-purpose
language—recasts scenario search as label retrieval. Erwin
de Gelder et al. [22] demonstrate a label-based system on
pre-annotated datasets; however, such labels are coarse and
scale poorly. Motional’s pipeline [23] follows a continual-
learning loop, maintaining a tag vocabulary that encodes basic
spatio-temporal relations and storing labels in a relational
database for efficient retrieval. While data-driven methods
yield realistic, behavior-grounded scenarios, limited coverage
makes rare edge cases a “needle in a haystack,” and outputs
are often descriptive rather than adversarially challenging. To
overcome these passive-mining limits, recent work applies
large foundation models [24], [25]. As surveyed by Gao et
al. [26], LLMs, VLMs, and diffusion models enable scenario
generation from high-level prompts—for example, “Create a
challenging scenario where a truck illegally overtakes a bicycle
on a rainy night”—and produce simulator-ready parameters.
The work by Manmohan Chandraker et al. [27] initially
focused on vision–language alignment tasks, and subsequently
extended similar ideas to data mining in autonomous driving.
They proposed an automated and iterative data engine for
autonomous driving [25] that realizes a complete closed-loop
pipeline, including issue identification, data mining, automatic
labeling, verification, and iterative model improvement. Works
such as ChatScene [28] have demonstrated the ability of
LLMs to understand complex spatial and behavioral rela-
tionships to produce diverse and contextually rich scenarios.
This approach holds immense promise for bridging the gap
between abstract human knowledge and concrete simulation
data. While still an emerging area, the key challenges include
ensuring the physical plausibility and controllability of gen-
erated scenarios and managing the significant computational
resources required by these large models. RefA V [8] is a
large-scale scenario-mining framework that houses 10,000
distinct natural-language queries describing the complex multi-
agent interactions present in the 1,000 driving logs of the
Argoverse 2 sensor suite. It exposes 28 handcrafted atomic
functions capable of recognizing trajectory states, expressing
relational predicates between a target agent and its surrounding
entities, and supporting basic Boolean logic. At its core,
the framework feeds the natural-language query, the atomic
function inventory, and carefully engineered prompts into an
LLM, which synthesizes an executable script composed ofthese atoms; running the script over the dataset then retrieves
the trajectories that satisfy the query, as shown in Fig. 1.
Zheng et al. [29] optimize atomic function generation via
independent LLM agents, while Chen et al. [30] further boost
RefA V through a fault-tolerant design coupled with more
effective prompt engineering. Building on RefA V , we decouple
Scenario Mining into a coarse-to-fine pipeline by combining
multimodal semantic filtering, few-shot LLM prompting, and
a fine-grained text–trajectory matcher, achieving gains in both
efficiency and accuracy.
III. METHOD
Fig. 2 overviews our framework, comprising three com-
ponents: (a) a CLIP-based multimodal coarse filter, (b)
knowledge-base-guided code generation for scenario mining,
and (c) a contrastive-learning-driven trajectory fine filter.
Specifically, to make the entire scenario-mining process fast
and accurate, and to compensate for the lack of RGB uti-
lization in the RefA V pipeline, we extract image features for
all video frames and text features for the query description,
select segments with higher cosine similarity, and restrict
precise mining to the subset of trajectories whose times-
tamps align with these segments. We build a scenario-mining
knowledge base from prior successful queries, storing triples
of{natural-language description, associated trajectories, exe-
cutable Python script}. At inference time, few-shot examples
from the knowledge base condition the LLM, improving its
handling of atomic functions involving spatial relationships.
We further train a fine-grained text–trajectory matcher via
contrastive learning on knowledge-base pairs; the candidate
trajectories surfaced by component (b) are scored by this
matcher to perform fine-level filtering.
A. CLIP-based Coarse Filtering
Within the original RefA V pipeline, scenario mining is
performed exclusively via scripts assembled from atomic
functions, a design that overlooks the direct correspondence
between natural-language queries and raw image frames. In
RefA V , the pipeline begins with 3D object detection to extract
each target’s class, heading, velocity, and related attributes,
assigns corresponding labels, and then waits for an LLM-
generated script to query them. The correctness of those
attributes rests wholly on the upstream detector’s performance,
and the elongated processing chain renders the connection
between natural-language queries and raw sensor data highly
indirect—raising the likelihood that crucial information will
be missed or inaccurately captured.
Inspired by advances in vision–language modeling, we
redesign the pipeline with a VLM-based coarse filtering stage
that directly compares natural-language queries with raw RGB
imagery, thereby temporally localizing the candidate regions
before any symbolic reasoning is invoked. Concretely, each
Argoverse 2 log spans 15s of synchronized sensor data cap-
tured by seven ring cameras at 20 Hz. We apply a sliding
window of lengthWseconds and strideSseconds to partition
the log into overlapping temporal clips. From each window
0
x-amz-checksum-crc32:am+OhQ==


0
x-amz-checksum-crc32:Qf/UHQ==

