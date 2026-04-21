R"(//------------------------------------------------------------------------------
)"
R"(// This file is contains additional mulmat kernels
)"
R"(// (and potentially other kernels).
)"
R"(//------------------------------------------------------------------------------
)"
R"(#ifdef cl_khr_fp16
)"
R"(#pragma OPENCL EXTENSION cl_khr_fp16 : enable
)"
R"(#elif defined(cl_amd_fp16)
)"
R"(#pragma OPENCL EXTENSION cl_amd_fp16 : enable
)"
R"(#else
)"
R"(#error "Half precision floating point not supportedby OpenCL implementation on your device."
)"
R"(#endif
)"
R"(
)"
R"(#ifdef cl_khr_subgroups
)"
R"(#pragma OPENCL EXTENSION cl_khr_subgroups : enable
)"
R"(#elif defined(cl_intel_subgroups)
)"
R"(#pragma OPENCL EXTENSION cl_intel_subgroups : enable
)"
R"(#else
)"
R"(#error "Subgroup not supported on your device."
)"
R"(#endif
)"
R"(
)"
R"(#ifdef cl_intel_required_subgroup_size
)"
R"(// Always use subgroup size of 32 on Intel.
)"
R"(#pragma OPENCL EXTENSION cl_intel_required_subgroup_size : enable
)"
R"(#define INTEL_GPU 1
)"
R"(#define REQD_SUBGROUP_SIZE_16 __attribute__((intel_reqd_sub_group_size(16)))
)"
R"(#define REQD_SUBGROUP_SIZE_32 __attribute__((intel_reqd_sub_group_size(32)))
)"
R"(#elif defined(cl_qcom_reqd_sub_group_size)
)"
R"(// Always use subgroups size of 64 on Adreno.
)"
R"(#pragma OPENCL EXTENSION cl_qcom_reqd_sub_group_size : enable
)"
R"(#define ADRENO_GPU 1
)"
R"(#define REQD_SUBGROUP_SIZE_64  __attribute__((qcom_reqd_sub_group_size("half")))
)"
R"(#define REQD_SUBGROUP_SIZE_128 __attribute__((qcom_reqd_sub_group_size("full")))
)"
R"(#else
)"
R"(// TODO: do not know how to choose subgroup size on other GPUs.
)"
R"(#error "Selecting subgroup size is not supported on your device."
)"
R"(#endif
)"
R"(
)"
R"(#define QK4_0                   32
)"
R"(#define QR4_0                   2
)"
R"(#define QK4_1                   32
)"
R"(#define QR4_1                   2
)"
R"(#define QK5_0                   32
)"
R"(#define QR5_0                   2
)"
R"(#define QK5_1                   32
)"
R"(#define QR5_1                   2
)"
R"(#define QK8_0                   32
)"
R"(#define QR8_0                   1
)"
R"(#define QK_K                    256
)"
R"(#define K_QUANTS_PER_ITERATION  2
)"
R"(
)"
R"(typedef char int8_t;
)"
R"(typedef uchar uint8_t;
)"
R"(typedef short int16_t;
)"
R"(typedef ushort uint16_t;
)"
R"(typedef int int32_t;
)"
R"(typedef uint uint32_t;
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// block_q4_0
)"
R"(//------------------------------------------------------------------------------
)"
R"(struct block_q4_0
)"
R"({
)"
R"(    half d;
)"
R"(    uint8_t qs[QK4_0 / 2];
)"
R"(};
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// block_q6_K
)"
R"(//------------------------------------------------------------------------------
)"
R"(// 6-bit quantization
)"
R"(// weight is represented as x = a * q
)"
R"(// 16 blocks of 16 elements each
)"
R"(// Effectively 6.5625 bits per weight
)"
R"(typedef struct {
)"
R"(    uint8_t ql[QK_K/2];      // quants, lower 4 bits
)"
R"(    uint8_t qh[QK_K/4];      // quants, upper 2 bits
)"
R"(    int8_t  scales[QK_K/16]; // scales, quantized with 8 bits
)"
R"(    half d;             // super-block scale
)"
R"(} block_q6_K;
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// These are the variant for matmatmul, based on the matvecmul kernel with
)"
R"(// flattened block_q4_0.
)"
R"(//------------------------------------------------------------------------------
)"
R"(
)"
R"(// Common dot prod.
)"
R"(inline float mm_block_q_4_0_dot_y_flat(
)"
R"(        global uchar * x,
)"
R"(        global half  * dh,
)"
R"(        float sumy,
)"
R"(        float16 yl,
)"
R"(        int il
)"
R"() {
)"
R"(    float           d   = *dh;
)"
R"(    global ushort * qs  = ((global ushort *)x + il/2);
)"
R"(    float           acc = 0.f;
)"
R"(
)"
R"(    acc += yl.s0 * (qs[0] & 0x000F);
)"
R"(    acc += yl.s1 * (qs[0] & 0x0F00);
)"
R"(    acc += yl.s8 * (qs[0] & 0x00F0);
)"
R"(    acc += yl.s9 * (qs[0] & 0xF000);
)"
R"(
)"
R"(    acc += yl.s2 * (qs[1] & 0x000F);
)"
R"(    acc += yl.s3 * (qs[1] & 0x0F00);
)"
R"(    acc += yl.sa * (qs[1] & 0x00F0);
)"
R"(    acc += yl.sb * (qs[1] & 0xF000);
)"
R"(
)"
R"(    acc += yl.s4 * (qs[2] & 0x000F);
)"
R"(    acc += yl.s5 * (qs[2] & 0x0F00);
)"
R"(    acc += yl.sc * (qs[2] & 0x00F0);
)"
R"(    acc += yl.sd * (qs[2] & 0xF000);
)"
R"(
)"
R"(    acc += yl.s6 * (qs[3] & 0x000F);
)"
R"(    acc += yl.s7 * (qs[3] & 0x0F00);
)"
R"(    acc += yl.se * (qs[3] & 0x00F0);
)"
R"(    acc += yl.sf * (qs[3] & 0xF000);
)"
R"(
)"
R"(    return d * (sumy * -8.f + acc);
)"
R"(}
)"
R"(
)"
R"(#undef N_DST
)"
R"(#undef N_SIMDGROUP
)"
R"(#undef N_SIMDWIDTH
)"
R"(
)"
R"(#ifdef INTEL_GPU
)"
R"(#define N_DST 8 // each SIMD group works on 8 rows (in weights matrix)
)"
R"(#define N_SIMDGROUP 1 // number of SIMD groups in a thread group
)"
R"(#define N_SIMDWIDTH 16 // assuming SIMD group size is 16
)"
R"(#elif defined (ADRENO_GPU)
)"
R"(#define N_DST 8
)"
R"(#define N_SIMDGROUP 1
)"
R"(#define N_SIMDWIDTH 64
)"
R"(#endif
)"
R"(//
)"
R"(// This variant performs 1d blocking with 8x output.
)"
R"(// Eeach simdgroup outputs 8 values on `n0` dim (row in the output matrix).
)"
R"(//
)"
R"(inline void mul_mat_q_n_f32_1d_8x_flat(
)"
R"(        global uchar * src0_q,
)"
R"(        global half  * src0_d,
)"
R"(        global float * src1,
)"
R"(        global float * dst,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        int ne10,
)"
R"(        int ne12,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int r2,
)"
R"(        int r3
)"
R"() {
)"
R"(    const int nb = ne00/QK4_0;
)"
R"(
)"
R"(    int r0 = get_group_id(0);
)"
R"(    int r1 = get_group_id(1);
)"
R"(    int im = get_group_id(2);
)"
R"(
)"
R"(    // (r0 * N_SIMDGROUP + get_sub_group_id()) is the linear global id of
)"
R"(    // a SIMD group in the grid. Each SIMD group produces N_DST values in the
)"
R"(    // result, hence uses nb blocks, i.e., the offset becomes first_row*nb.
)"
R"(    // Currently with llama2 7B, im is always 0.
)"
R"(    // TODO: how to handle im/gqa*(nb*ne0)?
)"
R"(    int first_row = (r0 * N_SIMDGROUP + get_sub_group_id()) * N_DST;
)"
R"(
)"
R"(    int i12 = im%ne12;
)"
R"(    int i13 = im/ne12;
)"
R"(
)"
R"(    // The number of scales is the same as the number of blocks.
)"
R"(    ulong offset0_d = first_row * nb + (i12/r2)*(nb*ne01) + (i13/r3)*(nb*ne01*ne02);
)"
R"(    // Each block contains QK4_0/2 uchars, hence offset for qs is as follows.
)"
R"(    ulong offset0_q = (first_row * nb + (i12/r2)*(nb*ne01) + (i13/r3)*(nb*ne01*ne02)) * QK4_0/2;
)"
R"(
)"
R"(    global uchar * x = (global uchar *) src0_q + offset0_q;
)"
R"(    global half  * d = (global half  *) src0_d + offset0_d;
)"
R"(    global float * y = (global float *) src1   + r1*ne10 + im*ne00*ne1;
)"
R"(
)"
R"(    float16 yl;
)"
R"(    float8 sumf = (float8)(0.f, 0.f, 0.f, 0.f, 0.f, 0.f, 0.f, 0.f);
)"
R"(
)"
R"(    int ix = get_sub_group_local_id()/2;
)"
R"(    int il = 8*(get_sub_group_local_id()%2);
)"
R"(
)"
R"(    global float * yb = y + ix*QK4_0 + il;
)"
R"(
)"
R"(    for (int ib = ix; ib < nb; ib += N_SIMDWIDTH/2) {
)"
R"(        float sumy = 0.f;
)"
R"(
)"
R"(        sumy += yb[0];
)"
R"(        sumy += yb[1];
)"
R"(        sumy += yb[2];
)"
R"(        sumy += yb[3];
)"
R"(        sumy += yb[4];
)"
R"(        sumy += yb[5];
)"
R"(        sumy += yb[6];
)"
R"(        sumy += yb[7];
)"
R"(
)"
R"(        sumy += yb[16];
)"
R"(        sumy += yb[17];
)"
R"(        sumy += yb[18];
)"
R"(        sumy += yb[19];
)"
R"(        sumy += yb[20];
)"
R"(        sumy += yb[21];
)"
R"(        sumy += yb[22];
)"
R"(        sumy += yb[23];
)"
R"(
)"
R"(        yl.s0 = yb[0];
)"
R"(        yl.s1 = yb[1]/256.f;
)"
R"(
)"
R"(        yl.s2 = yb[2];
)"
R"(        yl.s3 = yb[3]/256.f;
)"
R"(
)"
R"(        yl.s4 = yb[4];
)"
R"(        yl.s5 = yb[5]/256.f;
)"
R"(
)"
R"(        yl.s6 = yb[6];
)"
R"(        yl.s7 = yb[7]/256.f;
)"
R"(
)"
R"(        yl.s8 = yb[16]/16.f;
)"
R"(        yl.s9 = yb[17]/4096.f;
)"
R"(
)"
R"(        yl.sa = yb[18]/16.f;
)"
R"(        yl.sb = yb[19]/4096.f;
)"
R"(
)"
R"(        yl.sc = yb[20]/16.f;
)"
R"(        yl.sd = yb[21]/4096.f;
)"
R"(
)"
R"(        yl.se = yb[22]/16.f;
)"
R"(        yl.sf = yb[23]/4096.f;
)"
R"(
)"
R"(        sumf.s0 += mm_block_q_4_0_dot_y_flat(x + ib*QK4_0/2 + 0*nb*QK4_0/2, d + ib + 0*nb, sumy, yl, il);
)"
R"(        sumf.s1 += mm_block_q_4_0_dot_y_flat(x + ib*QK4_0/2 + 1*nb*QK4_0/2, d + ib + 1*nb, sumy, yl, il);
)"
R"(        sumf.s2 += mm_block_q_4_0_dot_y_flat(x + ib*QK4_0/2 + 2*nb*QK4_0/2, d + ib + 2*nb, sumy, yl, il);
)"
R"(        sumf.s3 += mm_block_q_4_0_dot_y_flat(x + ib*QK4_0/2 + 3*nb*QK4_0/2, d + ib + 3*nb, sumy, yl, il);
)"
R"(
)"
R"(        sumf.s4 += mm_block_q_4_0_dot_y_flat(x + ib*QK4_0/2 + 4*nb*QK4_0/2, d + ib + 4*nb, sumy, yl, il);
)"
R"(        sumf.s5 += mm_block_q_4_0_dot_y_flat(x + ib*QK4_0/2 + 5*nb*QK4_0/2, d + ib + 5*nb, sumy, yl, il);
)"
R"(        sumf.s6 += mm_block_q_4_0_dot_y_flat(x + ib*QK4_0/2 + 6*nb*QK4_0/2, d + ib + 6*nb, sumy, yl, il);
)"
R"(        sumf.s7 += mm_block_q_4_0_dot_y_flat(x + ib*QK4_0/2 + 7*nb*QK4_0/2, d + ib + 7*nb, sumy, yl, il);
)"
R"(
)"
R"(        yb += QK4_0 * (N_SIMDWIDTH/2);
)"
R"(    }
)"
R"(
)"
R"(    float8 tot = (float8)(
)"
R"(        sub_group_reduce_add(sumf.s0), sub_group_reduce_add(sumf.s1),
)"
R"(        sub_group_reduce_add(sumf.s2), sub_group_reduce_add(sumf.s3),
)"
R"(        sub_group_reduce_add(sumf.s4), sub_group_reduce_add(sumf.s5),
)"
R"(        sub_group_reduce_add(sumf.s6), sub_group_reduce_add(sumf.s7)
)"
R"(    );
)"
R"(
)"
R"(    if (get_sub_group_local_id() == 0) {
)"
R"(        if (first_row + 0 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 0] = tot.s0;
)"
R"(        }
)"
R"(        if (first_row + 1 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 1] = tot.s1;
)"
R"(        }
)"
R"(        if (first_row + 2 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 2] = tot.s2;
)"
R"(        }
)"
R"(        if (first_row + 3 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 3] = tot.s3;
)"
R"(        }
)"
R"(
)"
R"(        if (first_row + 4 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 4] = tot.s4;
)"
R"(        }
)"
R"(        if (first_row + 5 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 5] = tot.s5;
)"
R"(        }
)"
R"(        if (first_row + 6 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 6] = tot.s6;
)"
R"(        }
)"
R"(        if (first_row + 7 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 7] = tot.s7;
)"
R"(        }
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(#ifdef INTEL_GPU
)"
R"(REQD_SUBGROUP_SIZE_16
)"
R"(#elif defined (ADRENO_GPU)
)"
R"(REQD_SUBGROUP_SIZE_64
)"
R"(#endif
)"
R"(kernel void kernel_mul_mat_q4_0_f32_1d_8x_flat(
)"
R"(        global uchar * src0_q,
)"
R"(        global half  * src0_d,
)"
R"(        global float * src1,
)"
R"(        ulong offset1,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        int ne10,
)"
R"(        int ne12,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int r2,
)"
R"(        int r3
)"
R"() {
)"
R"(    src1 = (global float*)((global char*)src1 + offset1);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    mul_mat_q_n_f32_1d_8x_flat(src0_q, src0_d, src1, dst, ne00, ne01, ne02, ne10, ne12, ne0, ne1, r2, r3);
)"
R"(}
)"
R"(
)"
R"(#undef N_DST
)"
R"(#undef N_SIMDGROUP
)"
R"(#undef N_SIMDWIDTH
)"
R"(
)"
R"(#ifdef INTEL_GPU
)"
R"(#define N_DST 16 // each SIMD group works on 8 rows (in weights matrix)
)"
R"(#define N_SIMDGROUP 1 // number of SIMD groups in a thread group
)"
R"(#define N_SIMDWIDTH 16 // assuming SIMD group size is 16
)"
R"(#elif defined (ADRENO_GPU)
)"
R"(#define N_DST 16
)"
R"(#define N_SIMDGROUP 1
)"
R"(#define N_SIMDWIDTH 64
)"
R"(#endif
)"
R"(//
)"
R"(// This variant performs 1d blocking with 16x output.
)"
R"(// Eeach simdgroup outputs 16 values on `n0` dim (row in the output matrix).
)"
R"(//
)"
R"(inline void mul_mat_q_n_f32_1d_16x_flat(
)"
R"(        global uchar * src0_q,
)"
R"(        global half  * src0_d,
)"
R"(        global float * src1,
)"
R"(        global float * dst,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        int ne10,
)"
R"(        int ne12,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int r2,
)"
R"(        int r3
)"
R"() {
)"
R"(    const int nb = ne00/QK4_0;
)"
R"(
)"
R"(    int r0 = get_group_id(0);
)"
R"(    int r1 = get_group_id(1);
)"
R"(    int im = get_group_id(2);
)"
R"(
)"
R"(    // (r0 * N_SIMDGROUP + get_sub_group_id()) is the linear global id of
)"
R"(    // a SIMD group in the grid. Each SIMD group produces N_DST values in the
)"
R"(    // result, hence uses nb blocks, i.e., the offset becomes first_row*nb.
)"
R"(    // Currently with llama2 7B, im is always 0.
)"
R"(    // TODO: how to handle im/gqa*(nb*ne0)?
)"
R"(    int first_row = (r0 * N_SIMDGROUP + get_sub_group_id()) * N_DST;
)"
R"(
)"
R"(    int i12 = im%ne12;
)"
R"(    int i13 = im/ne12;
)"
R"(
)"
R"(    // The number of scales is the same as the number of blocks.
)"
R"(    ulong offset0_d = first_row * nb + (i12/r2)*(nb*ne01) + (i13/r3)*(nb*ne01*ne02);
)"
R"(    // Each block contains QK4_0/2 uchars, hence offset for qs is as follows.
)"
R"(    ulong offset0_q = (first_row * nb + (i12/r2)*(nb*ne01) + (i13/r3)*(nb*ne01*ne02)) * QK4_0/2;
)"
R"(
)"
R"(    global uchar * x = (global uchar *) src0_q + offset0_q;
)"
R"(    global half  * d = (global half  *) src0_d + offset0_d;
)"
R"(    global float * y = (global float *) src1   + r1*ne10 + im*ne00*ne1;
)"
R"(
)"
R"(    float16 yl;
)"
R"(    float16 sumf = (float16)(0.f, 0.f, 0.f, 0.f, 0.f, 0.f, 0.f, 0.f,
)"
R"(                             0.f, 0.f, 0.f, 0.f, 0.f, 0.f, 0.f, 0.f);
)"
R"(
)"
R"(    int ix = get_sub_group_local_id()/2;
)"
R"(    int il = 8*(get_sub_group_local_id()%2);
)"
R"(
)"
R"(    global float * yb = y + ix*QK4_0 + il;
)"
R"(
)"
R"(    for (int ib = ix; ib < nb; ib += N_SIMDWIDTH/2) {
)"
R"(        float sumy = 0.f;
)"
R"(
)"
R"(        sumy += yb[0];
)"
R"(        sumy += yb[1];
)"
R"(        sumy += yb[2];
)"
R"(        sumy += yb[3];
)"
R"(        sumy += yb[4];
)"
R"(        sumy += yb[5];
)"
R"(        sumy += yb[6];
)"
R"(        sumy += yb[7];
)"
R"(
)"
R"(        sumy += yb[16];
)"
R"(        sumy += yb[17];
)"
R"(        sumy += yb[18];
)"
R"(        sumy += yb[19];
)"
R"(        sumy += yb[20];
)"
R"(        sumy += yb[21];
)"
R"(        sumy += yb[22];
)"
R"(        sumy += yb[23];
)"
R"(
)"
R"(        yl.s0 = yb[0];
)"
R"(        yl.s1 = yb[1]/256.f;
)"
R"(
)"
R"(        yl.s2 = yb[2];
)"
R"(        yl.s3 = yb[3]/256.f;
)"
R"(
)"
R"(        yl.s4 = yb[4];
)"
R"(        yl.s5 = yb[5]/256.f;
)"
R"(
)"
R"(        yl.s6 = yb[6];
)"
R"(        yl.s7 = yb[7]/256.f;
)"
R"(
)"
R"(        yl.s8 = yb[16]/16.f;
)"
R"(        yl.s9 = yb[17]/4096.f;
)"
R"(
)"
R"(        yl.sa = yb[18]/16.f;
)"
R"(        yl.sb = yb[19]/4096.f;
)"
R"(
)"
R"(        yl.sc = yb[20]/16.f;
)"
R"(        yl.sd = yb[21]/4096.f;
)"
R"(
)"
R"(        yl.se = yb[22]/16.f;
)"
R"(        yl.sf = yb[23]/4096.f;
)"
R"(
)"
R"(        sumf.s0 += mm_block_q_4_0_dot_y_flat(x + ib*QK4_0/2 +  0*nb*QK4_0/2, d + ib +  0*nb, sumy, yl, il);
)"
R"(        sumf.s1 += mm_block_q_4_0_dot_y_flat(x + ib*QK4_0/2 +  1*nb*QK4_0/2, d + ib +  1*nb, sumy, yl, il);
)"
R"(        sumf.s2 += mm_block_q_4_0_dot_y_flat(x + ib*QK4_0/2 +  2*nb*QK4_0/2, d + ib +  2*nb, sumy, yl, il);
)"
R"(        sumf.s3 += mm_block_q_4_0_dot_y_flat(x + ib*QK4_0/2 +  3*nb*QK4_0/2, d + ib +  3*nb, sumy, yl, il);
)"
R"(
)"
R"(        sumf.s4 += mm_block_q_4_0_dot_y_flat(x + ib*QK4_0/2 +  4*nb*QK4_0/2, d + ib +  4*nb, sumy, yl, il);
)"
R"(        sumf.s5 += mm_block_q_4_0_dot_y_flat(x + ib*QK4_0/2 +  5*nb*QK4_0/2, d + ib +  5*nb, sumy, yl, il);
)"
R"(        sumf.s6 += mm_block_q_4_0_dot_y_flat(x + ib*QK4_0/2 +  6*nb*QK4_0/2, d + ib +  6*nb, sumy, yl, il);
)"
R"(        sumf.s7 += mm_block_q_4_0_dot_y_flat(x + ib*QK4_0/2 +  7*nb*QK4_0/2, d + ib +  7*nb, sumy, yl, il);
)"
R"(
)"
R"(        sumf.s8 += mm_block_q_4_0_dot_y_flat(x + ib*QK4_0/2 +  8*nb*QK4_0/2, d + ib +  8*nb, sumy, yl, il);
)"
R"(        sumf.s9 += mm_block_q_4_0_dot_y_flat(x + ib*QK4_0/2 +  9*nb*QK4_0/2, d + ib +  9*nb, sumy, yl, il);
)"
R"(        sumf.sa += mm_block_q_4_0_dot_y_flat(x + ib*QK4_0/2 + 10*nb*QK4_0/2, d + ib + 10*nb, sumy, yl, il);
)"
R"(        sumf.sb += mm_block_q_4_0_dot_y_flat(x + ib*QK4_0/2 + 11*nb*QK4_0/2, d + ib + 11*nb, sumy, yl, il);
)"
R"(
)"
R"(        sumf.sc += mm_block_q_4_0_dot_y_flat(x + ib*QK4_0/2 + 12*nb*QK4_0/2, d + ib + 12*nb, sumy, yl, il);
)"
R"(        sumf.sd += mm_block_q_4_0_dot_y_flat(x + ib*QK4_0/2 + 13*nb*QK4_0/2, d + ib + 13*nb, sumy, yl, il);
)"
R"(        sumf.se += mm_block_q_4_0_dot_y_flat(x + ib*QK4_0/2 + 14*nb*QK4_0/2, d + ib + 14*nb, sumy, yl, il);
)"
R"(        sumf.sf += mm_block_q_4_0_dot_y_flat(x + ib*QK4_0/2 + 15*nb*QK4_0/2, d + ib + 15*nb, sumy, yl, il);
)"
R"(
)"
R"(        yb += QK4_0 * (N_SIMDWIDTH/2);
)"
R"(    }
)"
R"(
)"
R"(    float16 tot = (float16)(
)"
R"(        sub_group_reduce_add(sumf.s0), sub_group_reduce_add(sumf.s1),
)"
R"(        sub_group_reduce_add(sumf.s2), sub_group_reduce_add(sumf.s3),
)"
R"(        sub_group_reduce_add(sumf.s4), sub_group_reduce_add(sumf.s5),
)"
R"(        sub_group_reduce_add(sumf.s6), sub_group_reduce_add(sumf.s7),
)"
R"(
)"
R"(        sub_group_reduce_add(sumf.s8), sub_group_reduce_add(sumf.s9),
)"
R"(        sub_group_reduce_add(sumf.sa), sub_group_reduce_add(sumf.sb),
)"
R"(        sub_group_reduce_add(sumf.sc), sub_group_reduce_add(sumf.sd),
)"
R"(        sub_group_reduce_add(sumf.se), sub_group_reduce_add(sumf.sf)
)"
R"(    );
)"
R"(
)"
R"(    if (get_sub_group_local_id() == 0) {
)"
R"(        if (first_row + 0 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 0] = tot.s0;
)"
R"(        }
)"
R"(        if (first_row + 1 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 1] = tot.s1;
)"
R"(        }
)"
R"(        if (first_row + 2 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 2] = tot.s2;
)"
R"(        }
)"
R"(        if (first_row + 3 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 3] = tot.s3;
)"
R"(        }
)"
R"(
)"
R"(        if (first_row + 4 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 4] = tot.s4;
)"
R"(        }
)"
R"(        if (first_row + 5 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 5] = tot.s5;
)"
R"(        }
)"
R"(        if (first_row + 6 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 6] = tot.s6;
)"
R"(        }
)"
R"(        if (first_row + 7 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 7] = tot.s7;
)"
R"(        }
)"
R"(
)"
R"(        if (first_row + 8 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 8] = tot.s8;
)"
R"(        }
)"
R"(        if (first_row + 9 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 9] = tot.s9;
)"
R"(        }
)"
R"(        if (first_row + 10 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 10] = tot.sa;
)"
R"(        }
)"
R"(        if (first_row + 11 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 11] = tot.sb;
)"
R"(        }
)"
R"(
)"
R"(        if (first_row + 12 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 12] = tot.sc;
)"
R"(        }
)"
R"(        if (first_row + 13 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 13] = tot.sd;
)"
R"(        }
)"
R"(        if (first_row + 14 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 14] = tot.se;
)"
R"(        }
)"
R"(        if (first_row + 15 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 15] = tot.sf;
)"
R"(        }
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(#ifdef INTEL_GPU
)"
R"(REQD_SUBGROUP_SIZE_16
)"
R"(#elif defined (ADRENO_GPU)
)"
R"(REQD_SUBGROUP_SIZE_64
)"
R"(#endif
)"
R"(kernel void kernel_mul_mat_q4_0_f32_1d_16x_flat(
)"
R"(        global uchar * src0_q,
)"
R"(        global half  * src0_d,
)"
R"(        global float * src1,
)"
R"(        ulong offset1,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        int ne10,
)"
R"(        int ne12,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int r2,
)"
R"(        int r3
)"
R"() {
)"
R"(    src1 = (global float*)((global char*)src1 + offset1);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    mul_mat_q_n_f32_1d_16x_flat(src0_q, src0_d, src1, dst, ne00, ne01, ne02, ne10, ne12, ne0, ne1, r2, r3);
)"
R"(}
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// kernel_mul_mat_q4_0_f32_flat_v0
)"
R"(//------------------------------------------------------------------------------
)"
R"(inline float block_q_4_0_dot_y_flat_v2(
)"
R"(    half   x,
)"
R"(    half   d,
)"
R"(    float  sumy,
)"
R"(    float4 yl
)"
R"() {
)"
R"(    uchar2 q = as_uchar2(x);
)"
R"(    float acc = 0.0f;
)"
R"(
)"
R"(    acc += (q.s0 & 0x0F) * yl.s0;
)"
R"(    acc += (q.s1 & 0x0F) * yl.s1;
)"
R"(
)"
R"(    acc += (q.s0 & 0xF0) * yl.s2;
)"
R"(    acc += (q.s1 & 0xF0) * yl.s3;
)"
R"(
)"
R"(    return d * (sumy * -8.f + acc);;
)"
R"(}
)"
R"(
)"
R"(inline float block_q_4_0_dot_y_flat_v4(
)"
R"(    float  x,
)"
R"(    half   d,
)"
R"(    float  sumy,
)"
R"(    float8 yl
)"
R"() {
)"
R"(    uchar4 q = as_uchar4(x);
)"
R"(    float acc = 0.0f;
)"
R"(
)"
R"(    acc += (q.s0 & 0x0F) * yl.s0;
)"
R"(    acc += (q.s1 & 0x0F) * yl.s1;
)"
R"(    acc += (q.s2 & 0x0F) * yl.s2;
)"
R"(    acc += (q.s3 & 0x0F) * yl.s3;
)"
R"(
)"
R"(    acc += (q.s0 & 0xF0) * yl.s4;
)"
R"(    acc += (q.s1 & 0xF0) * yl.s5;
)"
R"(    acc += (q.s2 & 0xF0) * yl.s6;
)"
R"(    acc += (q.s3 & 0xF0) * yl.s7;
)"
R"(
)"
R"(    return d * (sumy * -8.f + acc);;
)"
R"(}
)"
R"(
)"
R"(inline float block_q_4_0_dot_y_flat_v8(
)"
R"(    float2  x,
)"
R"(    half    d,
)"
R"(    float   sumy,
)"
R"(    float16 yl
)"
R"() {
)"
R"(    uchar8 q = as_uchar8(x);
)"
R"(    float acc = 0.0f;
)"
R"(
)"
R"(    acc += (q.s0 & 0x0F) * yl.s0;
)"
R"(    acc += (q.s1 & 0x0F) * yl.s1;
)"
R"(    acc += (q.s2 & 0x0F) * yl.s2;
)"
R"(    acc += (q.s3 & 0x0F) * yl.s3;
)"
R"(    acc += (q.s4 & 0x0F) * yl.s4;
)"
R"(    acc += (q.s5 & 0x0F) * yl.s5;
)"
R"(    acc += (q.s6 & 0x0F) * yl.s6;
)"
R"(    acc += (q.s7 & 0x0F) * yl.s7;
)"
R"(
)"
R"(    acc += (q.s0 & 0xF0) * yl.s8;
)"
R"(    acc += (q.s1 & 0xF0) * yl.s9;
)"
R"(    acc += (q.s2 & 0xF0) * yl.sa;
)"
R"(    acc += (q.s3 & 0xF0) * yl.sb;
)"
R"(    acc += (q.s4 & 0xF0) * yl.sc;
)"
R"(    acc += (q.s5 & 0xF0) * yl.sd;
)"
R"(    acc += (q.s6 & 0xF0) * yl.se;
)"
R"(    acc += (q.s7 & 0xF0) * yl.sf;
)"
R"(
)"
R"(    return d * (sumy * -8.f + acc);;
)"
R"(}
)"
R"(
)"
R"(#undef N_DST
)"
R"(#undef N_SIMDGROUP
)"
R"(#undef N_SIMDWIDTH
)"
R"(
)"
R"(#ifdef INTEL_GPU
)"
R"(#define THREADS_PER_BLK 4   // Number of threads per block, or each thread process 1/THREADS_PER_BLK of a block
)"
R"(#define N_DST           4
)"
R"(#define N_SIMDGROUP     1
)"
R"(#define N_SIMDWIDTH     16
)"
R"(#elif defined (ADRENO_GPU)
)"
R"(#define THREADS_PER_BLK 4
)"
R"(#define N_DST           4
)"
R"(#define N_SIMDGROUP     1
)"
R"(#define N_SIMDWIDTH     64
)"
R"(#endif
)"
R"(
)"
R"(#if THREADS_PER_BLK == 2                // Each thread processes 1/2 block
)"
R"(#   define ACT_TY                       float16
)"
R"(#   define Q_BLK_LD_TY                  float2
)"
R"(#   define block_q_4_0_dot_y_flat       block_q_4_0_dot_y_flat_v8
)"
R"(#elif THREADS_PER_BLK == 4              // Each thread processes 1/4 block
)"
R"(#   define ACT_TY                       float8
)"
R"(#   define Q_BLK_LD_TY                  float
)"
R"(#   define block_q_4_0_dot_y_flat       block_q_4_0_dot_y_flat_v4
)"
R"(#elif THREADS_PER_BLK == 8              // Each thread processes 1/8 block
)"
R"(#   define ACT_TY                       float4
)"
R"(#   define Q_BLK_LD_TY                  half
)"
R"(#   define block_q_4_0_dot_y_flat       block_q_4_0_dot_y_flat_v2
)"
R"(#endif
)"
R"(
)"
R"(#define BTYES_PER_THREAD_IN_BLK         (QK4_0/2/THREADS_PER_BLK)
)"
R"(
)"
R"(#if N_DST == 2
)"
R"(#   define  SUM_TY                      float2
)"
R"(#elif N_DST == 4
)"
R"(#   define  SUM_TY                      float4
)"
R"(#elif N_DST == 8
)"
R"(#   define  SUM_TY                      float8
)"
R"(#elif N_DST == 16
)"
R"(#   define  SUM_TY                      float16
)"
R"(#endif
)"
R"(
)"
R"(#ifdef INTEL_GPU
)"
R"(REQD_SUBGROUP_SIZE_16
)"
R"(#elif defined (ADRENO_GPU)
)"
R"(REQD_SUBGROUP_SIZE_64
)"
R"(#endif
)"
R"(kernel void kernel_mul_mat_q4_0_f32_flat_v0(
)"
R"(        global uchar * src0_q,
)"
R"(        global half  * src0_d,
)"
R"(        global float * src1,
)"
R"(        ulong offset1,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        int ne10,
)"
R"(        int ne12,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int r2,
)"
R"(        int r3
)"
R"() {
)"
R"(    src1 = (global float*)((global char*)src1 + offset1);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    const int nb = ne00/QK4_0;
)"
R"(
)"
R"(    int r0 = get_group_id(0);
)"
R"(    int r1 = get_group_id(1);
)"
R"(    int im = get_group_id(2);
)"
R"(
)"
R"(    int first_row = (r0 * N_SIMDGROUP + get_sub_group_id()) * N_DST;
)"
R"(
)"
R"(    int i12 = im%ne12;
)"
R"(    int i13 = im/ne12;
)"
R"(
)"
R"(    // The number of scales is the same as the number of blocks.
)"
R"(    ulong offset0_d = first_row * nb + (i12/r2)*(nb*ne01) + (i13/r3)*(nb*ne01*ne02);
)"
R"(    // Each block contains QK4_0/2 uchars, hence offset for qs is as follows.
)"
R"(    ulong offset0_q = (first_row * nb + (i12/r2)*(nb*ne01) + (i13/r3)*(nb*ne01*ne02)) * QK4_0/2;
)"
R"(
)"
R"(    global uchar * x = (global uchar *) src0_q + offset0_q;
)"
R"(    global half  * d = (global half  *) src0_d + offset0_d;
)"
R"(    global float * y = (global float *) src1   + r1*ne10 + im*ne00*ne1;
)"
R"(
)"
R"(    int ix = get_sub_group_local_id()/THREADS_PER_BLK;
)"
R"(    int il = get_sub_group_local_id()%THREADS_PER_BLK;
)"
R"(
)"
R"(    global float * yb = y + ix*QK4_0 + BTYES_PER_THREAD_IN_BLK*il;
)"
R"(
)"
R"(    // Registers for caching activation
)"
R"(    ACT_TY yl = 0.f;
)"
R"(
)"
R"(    // Registers for caching quants
)"
R"(    Q_BLK_LD_TY q_blk_0 = 0, q_blk_1 = 0;
)"
R"(#if N_DST == 4 || N_DST == 8 || N_DST == 16
)"
R"(    Q_BLK_LD_TY q_blk_2 = 0, q_blk_3 = 0;
)"
R"(#endif
)"
R"(#if N_DST == 8 || N_DST == 16
)"
R"(    Q_BLK_LD_TY q_blk_4 = 0, q_blk_5 = 0, q_blk_6 = 0, q_blk_7 = 0;
)"
R"(#endif
)"
R"(
)"
R"(    // Partial sum
)"
R"(    SUM_TY sumf = 0.f;
)"
R"(
)"
R"(    for (int ib = ix; ib < nb; ib += N_SIMDWIDTH/THREADS_PER_BLK) {
)"
R"(        float sumy = 0.f;
)"
R"(
)"
R"(        q_blk_0 = *(global Q_BLK_LD_TY*)(x + ib*QK4_0/2 + BTYES_PER_THREAD_IN_BLK*il + 0*nb*QK4_0/2);
)"
R"(        q_blk_1 = *(global Q_BLK_LD_TY*)(x + ib*QK4_0/2 + BTYES_PER_THREAD_IN_BLK*il + 1*nb*QK4_0/2);
)"
R"(#if N_DST == 4 || N_DST == 8 || N_DST == 16
)"
R"(        q_blk_2 = *(global Q_BLK_LD_TY*)(x + ib*QK4_0/2 + BTYES_PER_THREAD_IN_BLK*il + 2*nb*QK4_0/2);
)"
R"(        q_blk_3 = *(global Q_BLK_LD_TY*)(x + ib*QK4_0/2 + BTYES_PER_THREAD_IN_BLK*il + 3*nb*QK4_0/2);
)"
R"(#endif
)"
R"(#if N_DST == 8 || N_DST == 16
)"
R"(        q_blk_4 = (*(global Q_BLK_LD_TY*)(x + ib*QK4_0/2 + BTYES_PER_THREAD_IN_BLK*il + 4*nb*QK4_0/2));
)"
R"(        q_blk_5 = (*(global Q_BLK_LD_TY*)(x + ib*QK4_0/2 + BTYES_PER_THREAD_IN_BLK*il + 5*nb*QK4_0/2));
)"
R"(        q_blk_6 = (*(global Q_BLK_LD_TY*)(x + ib*QK4_0/2 + BTYES_PER_THREAD_IN_BLK*il + 6*nb*QK4_0/2));
)"
R"(        q_blk_7 = (*(global Q_BLK_LD_TY*)(x + ib*QK4_0/2 + BTYES_PER_THREAD_IN_BLK*il + 7*nb*QK4_0/2));
)"
R"(#endif
)"
R"(
)"
R"(        // Load activation
)"
R"(#if THREADS_PER_BLK == 2    // Each thread processes 1/2 block
)"
R"(        yl.s01234567 = *(global float8 *)(yb);
)"
R"(        yl.s89abcdef = *(global float8 *)(yb + 16);
)"
R"(
)"
R"(        sumy += yl.s0;
)"
R"(        sumy += yl.s1;
)"
R"(        sumy += yl.s2;
)"
R"(        sumy += yl.s3;
)"
R"(        sumy += yl.s4;
)"
R"(        sumy += yl.s5;
)"
R"(        sumy += yl.s6;
)"
R"(        sumy += yl.s7;
)"
R"(        sumy += yl.s8; yl.s8 /= 16.f;
)"
R"(        sumy += yl.s9; yl.s9 /= 16.f;
)"
R"(        sumy += yl.sa; yl.sa /= 16.f;
)"
R"(        sumy += yl.sb; yl.sb /= 16.f;
)"
R"(        sumy += yl.sc; yl.sc /= 16.f;
)"
R"(        sumy += yl.sd; yl.sd /= 16.f;
)"
R"(        sumy += yl.se; yl.se /= 16.f;
)"
R"(        sumy += yl.sf; yl.sf /= 16.f;
)"
R"(#elif THREADS_PER_BLK == 4  // Each thread processes 1/4 block
)"
R"(        yl.s0123 = *(global float4 *)(yb);
)"
R"(        yl.s4567 = *(global float4 *)(yb + 16);
)"
R"(
)"
R"(        sumy += yl.s0;
)"
R"(        sumy += yl.s1;
)"
R"(        sumy += yl.s2;
)"
R"(        sumy += yl.s3;
)"
R"(        sumy += yl.s4; yl.s4 /= 16.f;
)"
R"(        sumy += yl.s5; yl.s5 /= 16.f;
)"
R"(        sumy += yl.s6; yl.s6 /= 16.f;
)"
R"(        sumy += yl.s7; yl.s7 /= 16.f;
)"
R"(#elif THREADS_PER_BLK == 8  // Each thread processes 1/8 block
)"
R"(        yl.s01 = *(global float2 *)(yb);
)"
R"(        yl.s23 = *(global float2 *)(yb + 16);
)"
R"(
)"
R"(        sumy += yl.s0;
)"
R"(        sumy += yl.s1;
)"
R"(        sumy += yl.s2; yl.s2 /= 16.f;
)"
R"(        sumy += yl.s3; yl.s3 /= 16.f;
)"
R"(#endif
)"
R"(
)"
R"(        sumf.s0 += block_q_4_0_dot_y_flat(q_blk_0, *(d + ib + 0*nb), sumy, yl);
)"
R"(        sumf.s1 += block_q_4_0_dot_y_flat(q_blk_1, *(d + ib + 1*nb), sumy, yl);
)"
R"(#if N_DST == 4 || N_DST == 8 || N_DST == 16
)"
R"(        sumf.s2 += block_q_4_0_dot_y_flat(q_blk_2, *(d + ib + 2*nb), sumy, yl);
)"
R"(        sumf.s3 += block_q_4_0_dot_y_flat(q_blk_3, *(d + ib + 3*nb), sumy, yl);
)"
R"(#endif
)"
R"(#if N_DST == 8 || N_DST == 16
)"
R"(        sumf.s4 += block_q_4_0_dot_y_flat(q_blk_4, *(d + ib + 4*nb), sumy, yl);
)"
R"(        sumf.s5 += block_q_4_0_dot_y_flat(q_blk_5, *(d + ib + 5*nb), sumy, yl);
)"
R"(        sumf.s6 += block_q_4_0_dot_y_flat(q_blk_6, *(d + ib + 6*nb), sumy, yl);
)"
R"(        sumf.s7 += block_q_4_0_dot_y_flat(q_blk_7, *(d + ib + 7*nb), sumy, yl);
)"
R"(#endif
)"
R"(
)"
R"(        yb += QK4_0 * (N_SIMDWIDTH/THREADS_PER_BLK);
)"
R"(    }
)"
R"(
)"
R"(    SUM_TY tot = (SUM_TY)(
)"
R"(          sub_group_reduce_add(sumf.s0), sub_group_reduce_add(sumf.s1)
)"
R"(#if N_DST == 4 || N_DST == 8 || N_DST == 16
)"
R"(        , sub_group_reduce_add(sumf.s2), sub_group_reduce_add(sumf.s3)
)"
R"(#endif
)"
R"(#if N_DST == 8 || N_DST == 16
)"
R"(        , sub_group_reduce_add(sumf.s4), sub_group_reduce_add(sumf.s5)
)"
R"(        , sub_group_reduce_add(sumf.s6), sub_group_reduce_add(sumf.s7)
)"
R"(#endif
)"
R"(    );
)"
R"(
)"
R"(    if (get_sub_group_local_id() == 0) {
)"
R"(        if (first_row + 0 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 0] = tot.s0;
)"
R"(        }
)"
R"(        if (first_row + 1 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 1] = tot.s1;
)"
R"(        }
)"
R"(#if N_DST == 4 || N_DST == 8 || N_DST == 16
)"
R"(        if (first_row + 2 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 2] = tot.s2;
)"
R"(        }
)"
R"(        if (first_row + 3 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 3] = tot.s3;
)"
R"(        }
)"
R"(#endif
)"
R"(#if N_DST == 8 || N_DST == 16
)"
R"(        if (first_row + 4 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 4] = tot.s4;
)"
R"(        }
)"
R"(        if (first_row + 5 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 5] = tot.s5;
)"
R"(        }
)"
R"(        if (first_row + 6 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 6] = tot.s6;
)"
R"(        }
)"
R"(        if (first_row + 7 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 7] = tot.s7;
)"
R"(        }
)"
R"(#endif
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// Using image1d_buffer_t
)"
R"(
)"
R"(#if defined(cl_qcom_subgroup_shuffle)
)"
R"(#pragma OPENCL EXTENSION cl_qcom_subgroup_shuffle : enable
)"
R"(float qcom_sub_group_reduce_add(float sum) {
)"
R"(    sum += qcom_sub_group_shuffle_down(sum, 32, CLK_SUB_GROUP_SHUFFLE_WIDTH_WAVE_SIZE_QCOM, 0.f);
)"
R"(    sum += qcom_sub_group_shuffle_down(sum, 16, CLK_SUB_GROUP_SHUFFLE_WIDTH_WAVE_SIZE_QCOM, 0.f);
)"
R"(    sum += qcom_sub_group_shuffle_down(sum,  8, CLK_SUB_GROUP_SHUFFLE_WIDTH_WAVE_SIZE_QCOM, 0.f);
)"
R"(    sum += qcom_sub_group_shuffle_down(sum,  4, CLK_SUB_GROUP_SHUFFLE_WIDTH_WAVE_SIZE_QCOM, 0.f);
)"
R"(    sum += qcom_sub_group_shuffle_down(sum,  2, CLK_SUB_GROUP_SHUFFLE_WIDTH_WAVE_SIZE_QCOM, 0.f);
)"
R"(    sum += qcom_sub_group_shuffle_down(sum,  1, CLK_SUB_GROUP_SHUFFLE_WIDTH_WAVE_SIZE_QCOM, 0.f);
)"
R"(    return sum;
)"
R"(}
)"
R"(#define sub_group_reduce_add qcom_sub_group_reduce_add
)"
R"(#else
)"
R"(#define sub_group_reduce_add sub_group_reduce_add
)"
R"(#endif
)"
R"(
)"
R"(#undef THREADS_PER_BLK
)"
R"(#undef N_DST
)"
R"(#undef N_SIMDGROUP
)"
R"(#undef N_SIMDWIDTH
)"
R"(
)"
R"(#ifdef INTEL_GPU
)"
R"(#define THREADS_PER_BLK 4   // Number of threads per block, or each thread process 1/THREADS_PER_BLK of a block
)"
R"(#define N_DST           4
)"
R"(#define N_SIMDGROUP     1
)"
R"(#define N_SIMDWIDTH     16
)"
R"(#elif defined (ADRENO_GPU)
)"
R"(#define THREADS_PER_BLK 4
)"
R"(#define N_DST           4
)"
R"(#define N_SIMDGROUP     1
)"
R"(#define N_SIMDWIDTH     64
)"
R"(#endif
)"
R"(
)"
R"(#if THREADS_PER_BLK == 2                // Each thread processes 1/2 block
)"
R"(#   define ACT_TY                       float16
)"
R"(#   define Q_BLK_LD_TY                  float2
)"
R"(#   define EXTRACT_BLK_DATA(tmp, part)  *((float2*)&tmp + part)
)"
R"(#   define block_q_4_0_dot_y_flat       block_q_4_0_dot_y_flat_v8
)"
R"(#elif THREADS_PER_BLK == 4              // Each thread processes 1/4 block
)"
R"(#   define ACT_TY                       float8
)"
R"(#   define Q_BLK_LD_TY                  float
)"
R"(#   define EXTRACT_BLK_DATA(tmp, part)  *((float*)&tmp + part)
)"
R"(#   define block_q_4_0_dot_y_flat       block_q_4_0_dot_y_flat_v4
)"
R"(#elif THREADS_PER_BLK == 8              // Each thread processes 1/8 block
)"
R"(#   define ACT_TY                       float4
)"
R"(#   define Q_BLK_LD_TY                  half
)"
R"(#   define EXTRACT_BLK_DATA(tmp, part)  *((half*)&tmp + part)
)"
R"(#   define block_q_4_0_dot_y_flat       block_q_4_0_dot_y_flat_v2
)"
R"(#endif
)"
R"(
)"
R"(#define BTYES_PER_THREAD_IN_BLK         (QK4_0/2/THREADS_PER_BLK)
)"
R"(
)"
R"(#if N_DST == 2
)"
R"(#   define  SUM_TY                      float2
)"
R"(#elif N_DST == 4
)"
R"(#   define  SUM_TY                      float4
)"
R"(#elif N_DST == 8
)"
R"(#   define  SUM_TY                      float8
)"
R"(#elif N_DST == 16
)"
R"(#   define  SUM_TY                      float16
)"
R"(#endif
)"
R"(
)"
R"(#ifdef INTEL_GPU
)"
R"(REQD_SUBGROUP_SIZE_16
)"
R"(#elif defined (ADRENO_GPU)
)"
R"(REQD_SUBGROUP_SIZE_64
)"
R"(#endif
)"
R"(kernel void kernel_mul_mat_q4_0_f32_flat_img_v0(
)"
R"(        read_only image1d_buffer_t src0_q,
)"
R"(        read_only image1d_buffer_t src0_d,
)"
R"(        global float * src1,
)"
R"(        ulong offset1,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        int ne10,
)"
R"(        int ne12,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int r2,
)"
R"(        int r3
)"
R"() {
)"
R"(    src1 = (global float*)((global char*)src1 + offset1);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    const int nb = ne00/QK4_0;
)"
R"(
)"
R"(    int r0 = get_group_id(0);
)"
R"(    int r1 = get_group_id(1);
)"
R"(    int im = get_group_id(2);
)"
R"(
)"
R"(    int first_row = (r0 * N_SIMDGROUP + get_sub_group_id()) * N_DST;
)"
R"(
)"
R"(    int i12 = im%ne12;
)"
R"(    int i13 = im/ne12;
)"
R"(
)"
R"(    // The number of scales is the same as the number of blocks.
)"
R"(    ulong offset0_d = first_row * nb + (i12/r2)*(nb*ne01) + (i13/r3)*(nb*ne01*ne02);
)"
R"(    // Each block contains QK4_0/2 uchars, hence offset for qs is as follows.
)"
R"(    ulong offset0_q = first_row * nb + (i12/r2)*(nb*ne01) + (i13/r3)*(nb*ne01*ne02);
)"
R"(
)"
R"(    global float * y = (global float *) src1   + r1*ne10 + im*ne00*ne1;
)"
R"(
)"
R"(    int ix = get_sub_group_local_id()/THREADS_PER_BLK;
)"
R"(    int il = get_sub_group_local_id()%THREADS_PER_BLK;
)"
R"(
)"
R"(    global float * yb = y + ix*QK4_0 + BTYES_PER_THREAD_IN_BLK*il;
)"
R"(
)"
R"(    // Registers for caching activation
)"
R"(    ACT_TY yl = 0.f;
)"
R"(
)"
R"(    // Registers for caching quants
)"
R"(    Q_BLK_LD_TY q_blk_0 = 0, q_blk_1 = 0;
)"
R"(#if N_DST == 4 || N_DST == 8 || N_DST == 16
)"
R"(    Q_BLK_LD_TY q_blk_2 = 0, q_blk_3 = 0;
)"
R"(#endif
)"
R"(#if N_DST == 8 || N_DST == 16
)"
R"(    Q_BLK_LD_TY q_blk_4 = 0, q_blk_5 = 0, q_blk_6 = 0, q_blk_7 = 0;
)"
R"(#endif
)"
R"(
)"
R"(    // Partial sum
)"
R"(    SUM_TY sumf = 0.f;
)"
R"(
)"
R"(    for (int ib = ix; ib < nb; ib += N_SIMDWIDTH/THREADS_PER_BLK) {
)"
R"(        float sumy = 0.f;;
)"
R"(
)"
R"(        float4 tmp;
)"
R"(        tmp = read_imagef(src0_q, offset0_q + ib + 0*nb);
)"
R"(        q_blk_0 = EXTRACT_BLK_DATA(tmp, il);
)"
R"(        tmp = read_imagef(src0_q, offset0_q + ib + 1*nb);
)"
R"(        q_blk_1 = EXTRACT_BLK_DATA(tmp, il);
)"
R"(#if N_DST == 4 || N_DST == 8 || N_DST == 16
)"
R"(        tmp = read_imagef(src0_q, offset0_q + ib + 2*nb);
)"
R"(        q_blk_2 = EXTRACT_BLK_DATA(tmp, il);
)"
R"(        tmp = read_imagef(src0_q, offset0_q + ib + 3*nb);
)"
R"(        q_blk_3 = EXTRACT_BLK_DATA(tmp, il);
)"
R"(#endif
)"
R"(#if N_DST == 8 || N_DST == 16
)"
R"(        tmp = read_imagef(src0_q, offset0_q + ib + 4*nb);
)"
R"(        q_blk_4 = EXTRACT_BLK_DATA(tmp, il);
)"
R"(        tmp = read_imagef(src0_q, offset0_q + ib + 5*nb);
)"
R"(        q_blk_5 = EXTRACT_BLK_DATA(tmp, il);
)"
R"(        tmp = read_imagef(src0_q, offset0_q + ib + 6*nb);
)"
R"(        q_blk_6 = EXTRACT_BLK_DATA(tmp, il);
)"
R"(        tmp = read_imagef(src0_q, offset0_q + ib + 7*nb);
)"
R"(        q_blk_7 = EXTRACT_BLK_DATA(tmp, il);
)"
R"(#endif
)"
R"(
)"
R"(        // Load activation
)"
R"(#if THREADS_PER_BLK == 2    // Each thread processes 1/2 block
)"
R"(        yl.s01234567 = *(global float8 *)(yb);
)"
R"(        yl.s89abcdef = *(global float8 *)(yb + 16);
)"
R"(
)"
R"(        sumy += yl.s0;
)"
R"(        sumy += yl.s1;
)"
R"(        sumy += yl.s2;
)"
R"(        sumy += yl.s3;
)"
R"(        sumy += yl.s4;
)"
R"(        sumy += yl.s5;
)"
R"(        sumy += yl.s6;
)"
R"(        sumy += yl.s7;
)"
R"(        sumy += yl.s8; yl.s8 /= 16.f;
)"
R"(        sumy += yl.s9; yl.s9 /= 16.f;
)"
R"(        sumy += yl.sa; yl.sa /= 16.f;
)"
R"(        sumy += yl.sb; yl.sb /= 16.f;
)"
R"(        sumy += yl.sc; yl.sc /= 16.f;
)"
R"(        sumy += yl.sd; yl.sd /= 16.f;
)"
R"(        sumy += yl.se; yl.se /= 16.f;
)"
R"(        sumy += yl.sf; yl.sf /= 16.f;
)"
R"(#elif THREADS_PER_BLK == 4  // Each thread processes 1/4 block
)"
R"(        yl.s0123 = *(global float4 *)(yb);
)"
R"(        yl.s4567 = *(global float4 *)(yb + 16);
)"
R"(
)"
R"(        sumy += yl.s0;
)"
R"(        sumy += yl.s1;
)"
R"(        sumy += yl.s2;
)"
R"(        sumy += yl.s3;
)"
R"(        sumy += yl.s4; yl.s4 /= 16.f;
)"
R"(        sumy += yl.s5; yl.s5 /= 16.f;
)"
R"(        sumy += yl.s6; yl.s6 /= 16.f;
)"
R"(        sumy += yl.s7; yl.s7 /= 16.f;
)"
R"(#elif THREADS_PER_BLK == 8  // Each thread processes 1/8 block
)"
R"(        yl.s01 = *(global float2 *)(yb);
)"
R"(        yl.s23 = *(global float2 *)(yb + 16);
)"
R"(
)"
R"(        sumy += yl.s0;
)"
R"(        sumy += yl.s1;
)"
R"(        sumy += yl.s2; yl.s2 /= 16.f;
)"
R"(        sumy += yl.s3; yl.s3 /= 16.f;
)"
R"(#endif
)"
R"(
)"
R"(        sumf.s0 += block_q_4_0_dot_y_flat(q_blk_0, read_imageh(src0_d, offset0_d + ib + 0*nb).s0, sumy, yl);
)"
R"(        sumf.s1 += block_q_4_0_dot_y_flat(q_blk_1, read_imageh(src0_d, offset0_d + ib + 1*nb).s0, sumy, yl);
)"
R"(#if N_DST == 4 || N_DST == 8 || N_DST == 16
)"
R"(        sumf.s2 += block_q_4_0_dot_y_flat(q_blk_2, read_imageh(src0_d, offset0_d + ib + 2*nb).s0, sumy, yl);
)"
R"(        sumf.s3 += block_q_4_0_dot_y_flat(q_blk_3, read_imageh(src0_d, offset0_d + ib + 3*nb).s0, sumy, yl);
)"
R"(#endif
)"
R"(#if N_DST == 8 || N_DST == 16
)"
R"(        sumf.s4 += block_q_4_0_dot_y_flat(q_blk_4, read_imageh(src0_d, offset0_d + ib + 4*nb).s0, sumy, yl);
)"
R"(        sumf.s5 += block_q_4_0_dot_y_flat(q_blk_5, read_imageh(src0_d, offset0_d + ib + 5*nb).s0, sumy, yl);
)"
R"(        sumf.s6 += block_q_4_0_dot_y_flat(q_blk_6, read_imageh(src0_d, offset0_d + ib + 6*nb).s0, sumy, yl);
)"
R"(        sumf.s7 += block_q_4_0_dot_y_flat(q_blk_7, read_imageh(src0_d, offset0_d + ib + 7*nb).s0, sumy, yl);
)"
R"(#endif
)"
R"(
)"
R"(        yb += QK4_0 * (N_SIMDWIDTH/THREADS_PER_BLK);
)"
R"(    }
)"
R"(
)"
R"(    SUM_TY tot = (SUM_TY)(
)"
R"(          sub_group_reduce_add(sumf.s0), sub_group_reduce_add(sumf.s1)
)"
R"(#if N_DST == 4 || N_DST == 8 || N_DST == 16
)"
R"(        , sub_group_reduce_add(sumf.s2), sub_group_reduce_add(sumf.s3)
)"
R"(#endif
)"
R"(#if N_DST == 8 || N_DST == 16
)"
R"(        , sub_group_reduce_add(sumf.s4), sub_group_reduce_add(sumf.s5)
)"
R"(        , sub_group_reduce_add(sumf.s6), sub_group_reduce_add(sumf.s7)
)"
R"(#endif
)"
R"(    );
)"
R"(
)"
R"(    if (get_sub_group_local_id() == 0) {
)"
R"(        if (first_row + 0 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 0] = tot.s0;
)"
R"(        }
)"
R"(        if (first_row + 1 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 1] = tot.s1;
)"
R"(        }
)"
R"(#if N_DST == 4 || N_DST == 8 || N_DST == 16
)"
R"(        if (first_row + 2 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 2] = tot.s2;
)"
R"(        }
)"
R"(        if (first_row + 3 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 3] = tot.s3;
)"
R"(        }
)"
R"(#endif
)"
R"(#if N_DST == 8 || N_DST == 16
)"
R"(        if (first_row + 4 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 4] = tot.s4;
)"
R"(        }
)"
R"(        if (first_row + 5 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 5] = tot.s5;
)"
R"(        }
)"
R"(        if (first_row + 6 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 6] = tot.s6;
)"
R"(        }
)"
R"(        if (first_row + 7 < ne01) {
)"
R"(            dst[r1*ne0 + im*ne0*ne1 + first_row + 7] = tot.s7;
)"
R"(        }
)"
R"(#endif
)"
R"(    }
)"
R"(}
)"
R"(
)"
R"(//------------------------------------------------------------------------------
)"
R"(// kernel_mul_mv_q6_K_f32
)"
R"(//------------------------------------------------------------------------------
)"
R"(
)"
R"(#undef N_DST
)"
R"(#undef N_SIMDGROUP
)"
R"(#undef N_SIMDWIDTH
)"
R"(
)"
R"(#ifdef INTEL_GPU
)"
R"(#define N_DST 1 // number of rows each SIMD group works on
)"
R"(#define N_SIMDGROUP 2 // number of SIMD groups in a thread group
)"
R"(#define N_SIMDWIDTH 16 // SIMD group size
)"
R"(#elif defined (ADRENO_GPU)
)"
R"(#define N_DST 1
)"
R"(#define N_SIMDGROUP 2
)"
R"(#define N_SIMDWIDTH 64
)"
R"(#endif
)"
R"(
)"
R"(#define BLOCK_STRIDE (N_SIMDWIDTH/16) // number of blocks each subgroup processes
)"
R"(
)"
R"(#ifdef INTEL_GPU
)"
R"(REQD_SUBGROUP_SIZE_16
)"
R"(#elif defined (ADRENO_GPU)
)"
R"(REQD_SUBGROUP_SIZE_64
)"
R"(#endif
)"
R"(kernel void kernel_mul_mv_q6_K_f32(
)"
R"(        global void * src0,
)"
R"(        ulong offset0,
)"
R"(        global float * src1,
)"
R"(        ulong offset1,
)"
R"(        global float * dst,
)"
R"(        ulong offsetd,
)"
R"(        int ne00,
)"
R"(        int ne01,
)"
R"(        int ne02,
)"
R"(        int ne10,
)"
R"(        int ne12,
)"
R"(        int ne0,
)"
R"(        int ne1,
)"
R"(        int r2,
)"
R"(        int r3
)"
R"() {
)"
R"(    src0 = (global void*)((global char*)src0 + offset0);
)"
R"(    src1 = (global float*)((global char*)src1 + offset1);
)"
R"(    dst = (global float*)((global char*)dst + offsetd);
)"
R"(
)"
R"(    uchar kmask1 = 0x03;
)"
R"(    uchar kmask2 = 0x0C;
)"
R"(    uchar kmask3 = 0x30;
)"
R"(    uchar kmask4 = 0xC0;
)"
R"(
)"
R"(    int nb = ne00/QK_K;
)"
R"(
)"
R"(    int r0 = get_group_id(0);
)"
R"(    int r1 = get_group_id(1);
)"
R"(    int im = get_group_id(2);
)"
R"(
)"
R"(    int row = N_SIMDGROUP * r0 + get_sub_group_id();
)"
R"(
)"
R"(    int i12 = im%ne12;
)"
R"(    int i13 = im/ne12;
)"
R"(
)"
R"(    ulong offset_src0 = (i12/r2)*(nb*ne01) + (i13/r3)*(nb*ne01*ne02);
)"
R"(
)"
R"(    global block_q6_K * x = (global block_q6_K *) src0 + row*nb + offset_src0;
)"
R"(    global float      * yy = (global float     *) src1 + r1*ne10 + im*ne00*ne1;
)"
R"(
)"
R"(    float sumf = 0;
)"
R"(
)"
R"(    // For Q6_K quantization, 16 values forms a subblock, 16 subblock forms a
)"
R"(    // block. Values in a subblock shares a scale that is quantized with 8 bits;
)"
R"(    // the entire block shares a single floating point scale.
)"
R"(    // For work distribution, each thread processes a subblock (16 weights), hence
)"
R"(    // 16 threads process a (super) block -- a subgroup thus handles SIMDWIDTH/16
)"
R"(    // (super) blocks -- this is the block stride.
)"
R"(    // The 16 threads that process a (super) block are split into 2 portions, each has
)"
R"(    // 8 threads; each portion works on 8 subblocks.
)"
R"(    // For subgroup of 16 threads, the entire subgroup works on a single (super) block
)"
R"(    // before moving to the next (super) block. Thread0 - thread7 work on the
)"
R"(    // first 8 subblocks; thread8 - thread15 works on the last 8 subblocks.
)"
R"(    // Thread0 - thread3 work on subblocks 0, 2, 4, 6; thread4 - thread7 work on
)"
R"(    // subblocks 1, 3, 5, 7. Each thread does not work on an entire subblock, but
)"
R"(    // works on a total of 16 weight values.
)"
R"(    int tid  = get_sub_group_local_id()/BLOCK_STRIDE; // first block_stride groups have tid=0
)"
R"(    int ix   = get_sub_group_local_id()%BLOCK_STRIDE; // first block is 0..block_stride-1
)"
R"(    int ip   = tid/8;   // first or second half of (super) block (0 or 1)
)"
R"(    int il   = tid%8;   // each half has 8 parts, one per scale
)"
R"(    int n    = 4;       // 4 scales at a time (and 4 sums)
)"
R"(    int l0   = n*il;    // offset into half-block, 0..28
)"
R"(    int is   = 8*ip + l0/16; // 0, 1, 8, 9
)"
R"(
)"
R"(    int y_offset = 128*ip + l0;
)"
R"(    int q_offset_l = 64*ip + l0;
)"
R"(    int q_offset_h = 32*ip + l0;
)"
R"(
)"
R"(    for (int i = ix; i < nb; i += BLOCK_STRIDE) {
)"
R"(
)"
R"(        global uint8_t * q1 = x[i].ql + q_offset_l;
)"
R"(        global uint8_t * q2 = q1 + QK_K/8;
)"
R"(        global uint8_t * qh = x[i].qh + q_offset_h;
)"
R"(        global int8_t  * sc = x[i].scales + is;
)"
R"(
)"
R"(        global float * y = yy + i * QK_K + y_offset;
)"
R"(
)"
R"(        float dall = x[i].d;
)"
R"(
)"
R"(        float4 sums = {0.f, 0.f, 0.f, 0.f};
)"
R"(
)"
R"(        sums.s0 += y[0+ 0] * ((float)((q1[0] & 0xF) | ((qh[0] & kmask1) << 4)) - 32.f);
)"
R"(        sums.s1 += y[0+32] * ((float)((q2[0] & 0xF) | ((qh[0] & kmask2) << 2)) - 32.f);
)"
R"(        sums.s2 += y[0+64] * ((float)((q1[0]  >> 4) | ((qh[0] & kmask3) << 0)) - 32.f);
)"
R"(        sums.s3 += y[0+96] * ((float)((q2[0]  >> 4) | ((qh[0] & kmask4) >> 2)) - 32.f);
)"
R"(
)"
R"(        sums.s0 += y[1+ 0] * ((float)((q1[1] & 0xF) | ((qh[1] & kmask1) << 4)) - 32.f);
)"
R"(        sums.s1 += y[1+32] * ((float)((q2[1] & 0xF) | ((qh[1] & kmask2) << 2)) - 32.f);
)"
R"(        sums.s2 += y[1+64] * ((float)((q1[1]  >> 4) | ((qh[1] & kmask3) << 0)) - 32.f);
)"
R"(        sums.s3 += y[1+96] * ((float)((q2[1]  >> 4) | ((qh[1] & kmask4) >> 2)) - 32.f);
)"
R"(
)"
R"(        sums.s0 += y[2+ 0] * ((float)((q1[2] & 0xF) | ((qh[2] & kmask1) << 4)) - 32.f);
)"
R"(        sums.s1 += y[2+32] * ((float)((q2[2] & 0xF) | ((qh[2] & kmask2) << 2)) - 32.f);
)"
R"(        sums.s2 += y[2+64] * ((float)((q1[2]  >> 4) | ((qh[2] & kmask3) << 0)) - 32.f);
)"
R"(        sums.s3 += y[2+96] * ((float)((q2[2]  >> 4) | ((qh[2] & kmask4) >> 2)) - 32.f);
)"
R"(
)"
R"(        sums.s0 += y[3+ 0] * ((float)((q1[3] & 0xF) | ((qh[3] & kmask1) << 4)) - 32.f);
)"
R"(        sums.s1 += y[3+32] * ((float)((q2[3] & 0xF) | ((qh[3] & kmask2) << 2)) - 32.f);
)"
R"(        sums.s2 += y[3+64] * ((float)((q1[3]  >> 4) | ((qh[3] & kmask3) << 0)) - 32.f);
)"
R"(        sums.s3 += y[3+96] * ((float)((q2[3]  >> 4) | ((qh[3] & kmask4) >> 2)) - 32.f);
)"
R"(
)"
R"(        sumf += dall * (sums.s0 * sc[0] + sums.s1 * sc[2] + sums.s2 * sc[4] + sums.s3 * sc[6]);
)"
R"(    }
)"
R"(
)"
R"(    float tot = sub_group_reduce_add(sumf);
)"
R"(    if (get_sub_group_local_id() == 0) {
)"
R"(        dst[r1*ne0 + im*ne0*ne1 + row] = tot;
)"
R"(    }
)"
R"(}
)"
