# CADGPT

![Cloud Architecture Overview Diagram](CADGPT.drawio.png "Cloud Architecture Overview")

## Synopsis

CADGPT is an experimental system that leverages large language models (LLMs) to generate 3D CAD models from textual descriptions, integrating tightly with OnShape’s API to produce parametric models. It uses OpenAI’s structured outputs to create JSON-based feature definitions, translating user design intent into machine-readable CAD instructions. At its core is a dynamic, iterative feedback loop where the LLM refines models based on prior outputs and user input, supported by a suggestion model to propose improvements or alternatives. The backend is powered by a sophisticated AWS architecture, including numerous Lambda functions orchestrated via API Gateway endpoints, a Qdrant vector database for semantic retrieval, and a document database supporting retrieval-augmented generation (RAG) to enhance model responses. Together with preprocessing, prompt-handling modules, and RAG seeding, cadgpt enables a highly interactive, scalable, and intelligent CAD workflow that bridges natural language specifications with complex 3D modeling.

## Demo Video

The system was prompted to generate an m4 hex screw.

https://github.com/user-attachments/assets/5efa7093-8d70-4a54-8e38-72a7e650405c


