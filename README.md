# CADGPT

![Cloud Architecture Overview Diagram](CADGPT.drawio.png "Cloud Architecture Overview")

## Synopsis

CADGPT is an experimental project that leverages large language models (LLMs) to generate 3D CAD models directly from textual descriptions. It integrates with OnShape’s API to create and manipulate parametric models real-time and employs OpenAI’s structured outputs to produce JSON-based feature definitions, translating design intent into a machine-readable format that CAD software can interpret. The system uses a dynamic, iterative feedback loop, allowing the LLM to refine and adjust models based on prior outputs and user input, and incorporates a suggestion model to propose design improvements or alternatives. With preprocessing, prompt-handling modules, and retrieval-augmented generation (RAG), CADGPT aims to create a more intuitive and interactive CAD workflow, streamlining the design process and bridging the gap between natural language specifications and complex 3D modeling.

## Demo Video

The system was prompted to generate an m4 hex screw.

https://github.com/user-attachments/assets/5efa7093-8d70-4a54-8e38-72a7e650405c


