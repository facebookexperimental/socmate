#include "Vchip_top.h"
#include "Vchip_top___024root.h"
#include "verilated.h"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace {

constexpr int kWidth = 640;
constexpr int kHeight = 360;
constexpr int kPixelsPerFrame = kWidth * kHeight;
constexpr uint64_t kMaxDrainCycles = 2000000ULL;

struct Harness {
    Vchip_top top;
    uint64_t cycle = 0;
    std::vector<uint8_t> current_bytes;
    bool saw_tlast = false;

    void eval_low() {
        top.clk = 0;
        top.eval();
    }

    void tick() {
        top.clk = 0;
        top.eval();
        top.clk = 1;
        top.eval();
        if (top.m_axis_byte_tvalid && top.m_axis_byte_tready) {
            current_bytes.push_back(static_cast<uint8_t>(top.m_axis_byte_tdata & 0xff));
            if (top.m_axis_byte_tlast) {
                saw_tlast = true;
            }
        }
        cycle++;
    }

    void reset() {
        top.rst_n = 0;
        top.s_axis_pixel_tdata = 0;
        top.s_axis_pixel_tvalid = 0;
        top.s_axis_pixel_tlast = 0;
        top.s_axis_pixel_tuser = 0;
        top.s_axis_status_tdata = 0;
        top.s_axis_status_tvalid = 0;
        top.s_axis_status_tlast = 0;
        top.m_axis_byte_tready = 1;
        top.scan_en = 1;
        top.debug_mode = 0;
        for (int i = 0; i < 8; ++i) tick();
        top.rst_n = 1;
        for (int i = 0; i < 4; ++i) tick();
    }

    bool drive_frame(const uint8_t* pixels, int qp_sel) {
        current_bytes.clear();
        saw_tlast = false;

        for (int idx = 0; idx < kPixelsPerFrame; ++idx) {
            top.s_axis_pixel_tdata = pixels[idx];
            top.s_axis_pixel_tvalid = 1;
            top.s_axis_pixel_tlast = (idx == kPixelsPerFrame - 1) ? 1 : 0;
            top.s_axis_pixel_tuser = static_cast<uint8_t>(((qp_sel & 0x3) << 1) | (idx == 0 ? 1 : 0));

            uint64_t guard = 0;
            do {
                tick();
                if (++guard > kMaxDrainCycles) {
                    std::cerr << "timeout waiting for pixel ready at index " << idx << "\n";
                    dump_status("stall");
                    return false;
                }
            } while (!top.s_axis_pixel_tready);
        }

        top.s_axis_pixel_tvalid = 0;
        top.s_axis_pixel_tlast = 0;
        top.s_axis_pixel_tuser = 0;

        for (uint64_t guard = 0; guard < kMaxDrainCycles; ++guard) {
            if (saw_tlast) {
                return true;
            }
            tick();
        }
        std::cerr << "timeout waiting for output tlast after " << current_bytes.size() << " bytes\n";
        dump_status("drain-timeout");
        return !current_bytes.empty() && top.s_axis_pixel_tready && top.ingest_frame_done &&
               !top.m_axis_byte_tvalid && !top.packer_active && !top.deblock_active;
    }

    bool clean() const {
        return top.error_flag == 0 &&
               top.ingest_error_flag == 0 &&
               top.cavlc_code_overflow_assert == 0 &&
               top.packer_overflow_assert == 0 &&
               top.fifo_overflow_assert == 0;
    }

    static uint32_t get_bits(const WData* words, int lsb, int width) {
        uint64_t value = 0;
        for (int bit = 0; bit < width; ++bit) {
            int src = lsb + bit;
            uint32_t word = words[src / 32];
            uint32_t b = (word >> (src % 32)) & 1U;
            value |= static_cast<uint64_t>(b) << bit;
        }
        return static_cast<uint32_t>(value);
    }

    void dump_status(const char* label) const {
        const WData* deblock_out = top.rootp->chip_top__DOT__u_deblock_filter__DOT__out_data_q.data();
        std::cerr << label
                  << " cycle=" << cycle
                  << " bytes=" << current_bytes.size()
                  << " pixel_ready=" << static_cast<int>(top.s_axis_pixel_tready)
                  << " byte_valid=" << static_cast<int>(top.m_axis_byte_tvalid)
                  << " byte_last=" << static_cast<int>(top.m_axis_byte_tlast)
                  << " busy=" << static_cast<int>(top.busy)
                  << " frame_done=" << static_cast<int>(top.frame_done)
                  << " error=" << static_cast<int>(top.error_flag)
                  << " stall=" << static_cast<int>(top.stall_req)
                  << " drain=" << static_cast<int>(top.drain_req)
                  << " ingest_busy=" << static_cast<int>(top.ingest_busy)
                  << " ingest_done=" << static_cast<int>(top.ingest_frame_done)
                  << " ingest_error=" << static_cast<int>(top.ingest_error_flag)
                  << " transform_active=" << static_cast<int>(top.transform_active)
                  << " cavlc_active=" << static_cast<int>(top.cavlc_active)
                  << " cavlc_state=" << static_cast<int>(top.rootp->chip_top__DOT__u_cavlc_enc__DOT__state_q)
                  << " cavlc_chunk=" << static_cast<int>(top.rootp->chip_top__DOT__u_cavlc_enc__DOT__chunk_id_q)
                  << " cavlc_final=" << static_cast<int>(top.rootp->chip_top__DOT__u_cavlc_enc__DOT__final_chunk_q)
                  << " cavlc_len=" << static_cast<int>(top.rootp->chip_top__DOT__u_cavlc_enc__DOT__code_len_q)
                  << " packer_active=" << static_cast<int>(top.packer_active)
                  << " deblock_active=" << static_cast<int>(top.deblock_active)
                  << " deblock_state=" << static_cast<int>(top.rootp->chip_top__DOT__u_deblock_filter__DOT__state_q)
                  << " deblock_next=" << static_cast<int>(top.rootp->chip_top__DOT__u_deblock_filter__DOT__state_next)
                  << " fifo_full=" << static_cast<int>(top.fifo_full)
                  << " fifo_almost_full=" << static_cast<int>(top.fifo_almost_full)
                  << " fifo_empty=" << static_cast<int>(top.fifo_empty)
                  << " cavlc_protocol=" << static_cast<int>(top.cavlc_protocol_assert)
                  << " cavlc_overflow=" << static_cast<int>(top.cavlc_code_overflow_assert)
                  << " packer_overflow=" << static_cast<int>(top.packer_overflow_assert)
                  << " fifo_overflow=" << static_cast<int>(top.fifo_overflow_assert)
                  << " mb_valid=" << static_cast<int>(top.rootp->chip_top__DOT__u_axis_frame_ingest__DOT__m_axis_mb_tvalid_q)
                  << " ctx_valid=" << static_cast<int>(top.rootp->chip_top__DOT__u_recon_context_store__DOT__m_axis_context_tvalid_reg)
                  << " ctx_to_intra_ready=" << static_cast<int>(top.rootp->chip_top__DOT__w_recon_context_store_m_axis_context_to_intra_predict_s_axis_context_tready)
                  << " intra_mb_valid=" << static_cast<int>(top.rootp->chip_top__DOT__u_intra_predict__DOT__mb_valid_q)
                  << " intra_ctx_valid=" << static_cast<int>(top.rootp->chip_top__DOT__u_intra_predict__DOT__ctx_valid_q)
                  << " intra_join_ready=" << static_cast<int>(top.rootp->chip_top__DOT__u_intra_predict__DOT__join_ready)
                  << " intra_out_valid=" << static_cast<int>(top.rootp->chip_top__DOT__u_intra_predict__DOT__candidates_valid_q)
                  << " transform_out_valid=" << static_cast<int>(top.rootp->chip_top__DOT__u_transform_select__DOT__out_valid_q)
                  << " quant_s0=" << static_cast<int>(top.rootp->chip_top__DOT__u_quantize_select__DOT__stage0_valid_q)
                  << " quant_s1=" << static_cast<int>(top.rootp->chip_top__DOT__u_quantize_select__DOT__stage1_valid_q)
                  << " mode_valid=" << static_cast<int>(top.rootp->chip_top__DOT__u_mode_decision__DOT__m_valid_reg)
                  << " zigzag_in_valid=" << static_cast<int>(top.rootp->chip_top__DOT__u_zigzag_rle_select__DOT__in_valid_reg)
                  << " zigzag_out_valid=" << static_cast<int>(top.rootp->chip_top__DOT__u_zigzag_rle_select__DOT__out_valid_reg)
                  << " cavlc_rle_valid=" << static_cast<int>(top.rootp->chip_top__DOT__u_cavlc_enc__DOT__rle_valid_q)
                  << " cavlc_out_valid=" << static_cast<int>(top.rootp->chip_top__DOT__u_cavlc_enc__DOT__out_valid_q)
                  << " packer_out_valid=" << static_cast<int>(top.rootp->chip_top__DOT__u_block_packer__DOT__out_valid_q)
                  << " fifo_out_valid=" << static_cast<int>(top.rootp->chip_top__DOT__u_output_byte_fifo__DOT__m_axis_byte_tvalid_reg)
                  << " deblock_out_valid=" << static_cast<int>(top.rootp->chip_top__DOT__u_deblock_filter__DOT__out_valid_q)
                  << " deblock_out_fire=" << static_cast<int>(top.rootp->chip_top__DOT__u_deblock_filter__DOT__out_fire)
                  << " deblock_x=" << get_bits(deblock_out, 512, 7)
                  << " deblock_y=" << get_bits(deblock_out, 519, 6)
                  << " deblock_last_meta=" << get_bits(deblock_out, 528, 1)
                  << " recon_exp_x=" << static_cast<int>(top.rootp->chip_top__DOT__u_recon_context_store__DOT__exp_upd_x)
                  << " recon_exp_y=" << static_cast<int>(top.rootp->chip_top__DOT__u_recon_context_store__DOT__exp_upd_y)
                  << " recon_ctx_x=" << static_cast<int>(top.rootp->chip_top__DOT__u_recon_context_store__DOT__ctx_x)
                  << " recon_ctx_y=" << static_cast<int>(top.rootp->chip_top__DOT__u_recon_context_store__DOT__ctx_y)
                  << " recon_expected_last=" << static_cast<int>(top.rootp->chip_top__DOT__u_recon_context_store__DOT__expected_last)
                  << "\n";
    }
};

std::string frame_path(const std::string& out_dir, int qp, int frame_idx) {
    std::ostringstream oss;
    oss << out_dir << "/verilator_qp" << qp << "_frame" << frame_idx << ".bin";
    return oss.str();
}

int qp_value(int qp_sel) {
    if (qp_sel == 0) return 24;
    if (qp_sel == 1) return 36;
    if (qp_sel == 2) return 48;
    return -1;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 5) {
        std::cerr << "usage: " << argv[0] << " input.raw frames qp_sel out_dir\n";
        return 2;
    }

    const std::string input_path = argv[1];
    const int frames = std::atoi(argv[2]);
    const int qp_sel = std::atoi(argv[3]);
    const int qp = qp_value(qp_sel);
    const std::string out_dir = argv[4];
    if (frames <= 0 || qp < 0) {
        std::cerr << "invalid frames or qp_sel\n";
        return 2;
    }

    std::ifstream in(input_path, std::ios::binary);
    if (!in) {
        std::cerr << "failed to open " << input_path << "\n";
        return 2;
    }
    std::vector<uint8_t> pixels(static_cast<size_t>(frames) * kPixelsPerFrame);
    in.read(reinterpret_cast<char*>(pixels.data()), static_cast<std::streamsize>(pixels.size()));
    if (in.gcount() != static_cast<std::streamsize>(pixels.size())) {
        std::cerr << "input raw is too short\n";
        return 2;
    }

    Harness h;
    h.reset();

    std::vector<size_t> byte_counts;
    for (int frame = 0; frame < frames; ++frame) {
        const uint8_t* ptr = pixels.data() + static_cast<size_t>(frame) * kPixelsPerFrame;
        if (!h.drive_frame(ptr, qp_sel)) {
            return 1;
        }
        if (!h.clean()) {
            std::cerr << "top error/assertion flags set after frame " << frame << "\n";
            return 1;
        }
        const std::string path = frame_path(out_dir, qp, frame);
        std::ofstream out(path, std::ios::binary);
        out.write(reinterpret_cast<const char*>(h.current_bytes.data()),
                  static_cast<std::streamsize>(h.current_bytes.size()));
        byte_counts.push_back(h.current_bytes.size());
        std::cerr << "frame " << frame << " qp " << qp << " bytes " << h.current_bytes.size()
                  << " cycles " << h.cycle << "\n";
    }

    std::ostringstream json;
    json << "{\n";
    json << "  \"qp\": " << qp << ",\n";
    json << "  \"qp_sel\": " << qp_sel << ",\n";
    json << "  \"frames\": " << frames << ",\n";
    json << "  \"width\": " << kWidth << ",\n";
    json << "  \"height\": " << kHeight << ",\n";
    json << "  \"cycles\": " << h.cycle << ",\n";
    json << "  \"cavlc_protocol_assert\": " << static_cast<int>(h.top.cavlc_protocol_assert) << ",\n";
    json << "  \"cavlc_code_overflow_assert\": " << static_cast<int>(h.top.cavlc_code_overflow_assert) << ",\n";
    json << "  \"packer_overflow_assert\": " << static_cast<int>(h.top.packer_overflow_assert) << ",\n";
    json << "  \"fifo_overflow_assert\": " << static_cast<int>(h.top.fifo_overflow_assert) << ",\n";
    json << "  \"frame_bytes\": [";
    size_t total = 0;
    for (size_t i = 0; i < byte_counts.size(); ++i) {
        if (i) json << ", ";
        json << byte_counts[i];
        total += byte_counts[i];
    }
    json << "],\n";
    json << "  \"bytes\": " << total << ",\n";
    json << "  \"bpp\": " << (8.0 * static_cast<double>(total) /
                               static_cast<double>(frames * kPixelsPerFrame)) << "\n";
    json << "}\n";

    std::ofstream meta(out_dir + "/verilator_qp" + std::to_string(qp) + ".json");
    meta << json.str();
    return 0;
}
