# Quipu Vision — Judge video (English, maximum 5 minutes)

**Use only actual captures and results. Do not show AWS as completed unless demonstrated.**

| Time | On screen | Suggested narration |
|---|---|---|
| 0:00–0:30 | Title, developer/team identification | “This is Quipu Vision, a human-in-the-loop visual inspection workflow using OpenCV 5 and AegisForge A2A. It helps distinguish an initial visual change from a spatially persistent one.” |
| 0:30–1:00 | Architecture diagram | “The client submits images, OpenCV 5 measures differences, a workflow audits evidence, and the system requests a next observation or human review. This is deterministic orchestration; we do not claim automatic camera control or object recognition.” |
| 1:00–1:55 | `http://127.0.0.1:8002/demo/vision`: reference + observed images | “Here, the Dockerized OpenCV 5 runtime detects a changed pixel region, shows its coordinates, and requests a second observation. The evidence overlay and quality advisories help the operator interpret the result.” |
| 1:55–2:45 | Add follow-up, run verify; show verified result and exported trace | “With the third observation, the workflow independently re-examines the image and identifies a persistent changed region. It recommends human review. Persistence is not object identity or a safety certificate.” |
| 2:45–3:40 | Evaluation Lab 24 cases and thresholds 32 vs 40 | “We evaluated 24 labeled synthetic scenarios. The baseline reached 75 percent initial accuracy; an exploratory threshold reached 79.17 percent, with unchanged persistence F1 of 88.89 percent. Lighting, shadows, glare and weak contrast remain failure modes. No test-set generalization is claimed.” |
| 3:40–4:25 | Docker terminal (`docker ps`, version, endpoint) and **actual** AWS console/probe/log if available | “The application runs in a Linux Docker container with OpenCV 5. [IF ACTUALLY VERIFIED: We also deployed the `vision_lambda` container to AWS Lambda and executed this signed A2A image request; here is the CloudWatch evidence.] [IF NOT VERIFIED: AWS architecture was prepared, but deployment remains unverified.]” |
| 4:25–5:00 | Limitations, GitHub repositories, how to access judge demo | “We provide the source, reproducibility steps, failure analysis and an auditable decision trail. Image change alone does not prove safety. Our planned next step is broader real-world evaluation. Thank you.” |

Video capture checklist: use clearly legible fonts, do not expose API credentials or private account details, show actual plugin `0.4.0` and OpenCV `5.0.0`, identify UI `0.5 candidate`, include accessible project URL and final judge demo instructions.
