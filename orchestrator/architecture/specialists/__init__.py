# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Architecture specialist agents.

Each specialist is a standalone async function that can be called directly
(via MCP tools / Claude CLI) or wrapped as a LangGraph node (Phase 3).
"""
