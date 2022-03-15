from super_gradients.training.sg_model import SgModel
from typing import Union
from torch import nn
from super_gradients.common.abstractions.abstract_logger import get_logger
from super_gradients.training import utils as core_utils
from super_gradients.training.utils import sg_model_utils
from super_gradients.training.utils.checkpoint_utils import read_ckpt_state_dict, load_checkpoint_to_model, \
    load_pretrained_weights
from super_gradients.training.pretrained_models import PRETRAINED_NUM_CLASSES
from super_gradients.training.models.kd_modules.kd_module import KDModule
from super_gradients.training.models import SgModule

logger = get_logger(__name__)


class KDModel(SgModel):
    """
    KDModel

    This class extends SgModel to support knowledge distillation.
    """
    def build_model(self,
                    student_architecture: Union[str, SgModule],
                    teacher_architecture: Union[str, nn.Module],
                    student_arch_params={},
                    teacher_arch_params={},
                    teacher_checkpoint_path: str = None,
                    load_kd_model_checkpoint: bool = False,
                    kd_model_source_ckpt_folder_name: str = None,
                    kd_model_external_checkpoint_path: str = None,
                    run_teacher_on_eval: bool = False,
                    ):
        """
        :param student_architecture:       Defines the student's architecture from models/ALL_ARCHITECTURES
        :param teacher_architecture:       Defines the teacher's architecture from models/ALL_ARCHITECTURES
        :param student_arch_params:        Architecture H.P. e.g.: block, num_blocks, num_classes, etc for student net.
        :param teacher_arch_params:        Architecture H.P. e.g.: block, num_blocks, num_classes, etc for teacher net.
        :param teacher_checkpoint_path:    Local path to the teacher's checkpoint. Note that when passing pretrained_weights
                                           through teacher_arch_params these weights will be overridden by the
                                           pretrained checkpoint.
        :param load_kd_model_checkpoint:   Whether to load an entire KDModule checkpoint (used to continue KD training)
        :param kd_model_source_ckpt_folder_name: Folder name to load an entire KDModule checkpoint from
            (self.experiment_name if none is given) to resume KD training
        :param kd_model_external_checkpoint_path: The path to the external checkpoint to be loaded. Can be absolute or relative
                                           (ie: path/to/checkpoint.pth). If provided, will automatically attempt to
                                           load the checkpoint even if the load_checkpoint flag is not provided
        :param run_teacher_on_eval:   Whether to run self.teacher at eval mode regardless of self.train(mode)
        :return:
        """

        kd_model_arch_params = {}

        # DERIVE NUMBER OF CLASSES FROM DATASET INTERFACE IF NOT SPECIFIED
        if 'num_classes' not in student_arch_params.keys():
            if self.dataset_interface is None:
                raise Exception('Error', 'Number of classes not defined in students arch params and dataset is not '
                                         'defined')
            else:
                student_arch_params['num_classes'] = len(self.classes)

        # ASSIGN STUDENT'S NUM_CLASSES TO TEACHER AND MAIN KD MODULE ARCH PARAMS
        kd_model_arch_params['num_classes'] = student_arch_params['num_classes']
        teacher_arch_params['num_classes'] = student_arch_params['num_classes']

        student_arch_params = core_utils.HpmStruct(**student_arch_params)
        teacher_arch_params = core_utils.HpmStruct(**teacher_arch_params)

        # IF PRETRAINED WEIGHTS ARE SPECIFIED FOR STUDENT, ASSIGN IT'S ARCH PARAMS NEW NUM CLASSES SO ITD BE POSSIBLE
        # TO LOAD THEM
        student_pretrained_weights = core_utils.get_param(student_arch_params, 'pretrained_weights', default_val=None)
        if student_pretrained_weights is not None:
            student_arch_params.num_classes = PRETRAINED_NUM_CLASSES[student_pretrained_weights]

        student_net, _ = sg_model_utils.instantiate_net(student_architecture, student_arch_params)
        teacher_net, _ = sg_model_utils.instantiate_net(teacher_architecture, teacher_arch_params)

        # IF TEACHER LOCAL CKPT IS GIVEN, ALWAYS LOAD ITS EMA IF IT EXISTS
        if teacher_checkpoint_path is not None:
            load_teachers_ema = 'ema_net' in read_ckpt_state_dict(teacher_checkpoint_path).keys()
            load_checkpoint_to_model(ckpt_local_path=teacher_checkpoint_path,
                                     load_backbone=False,
                                     net=teacher_net,
                                     strict='no_key_matching',
                                     load_weights_only=True,
                                     load_ema_as_net=load_teachers_ema)

        teacher_pretrained_weights = core_utils.get_param(teacher_arch_params, 'pretrained_weights', default_val=None)

        if teacher_pretrained_weights is not None:
            teacher_pretrained_num_classes = PRETRAINED_NUM_CLASSES[teacher_pretrained_weights]

            # MAKE SURE TEACHER'S PRETRAINED NUM CLASSES EQUALS TO THE ONES BELONGING TO STUDENT AS WE CAN'T REPLACE
            # THE TEACHER'S HEAD
            if teacher_pretrained_num_classes != kd_model_arch_params['num_classes']:
                raise ValueError(
                    "Pretrained dataset number of classes in teacher's arch params must be equal to the student's "
                    "number of classes.")
            if teacher_checkpoint_path is not None:
                if teacher_pretrained_weights:
                    logger.warning(
                        teacher_checkpoint_path + " checkpoint is "
                                                  "overriding " + teacher_pretrained_weights + "for teacher model")

            load_pretrained_weights(teacher_net, teacher_architecture, teacher_pretrained_weights)

        # CHECK THAT TEACHER NETWORK HOLDS KNOWLEDGE FOR THE STUDENT TO LEARN FROM
        if not (teacher_pretrained_weights or teacher_checkpoint_path or load_kd_model_checkpoint):
            raise ValueError("Expected: at least one of: teacher_pretrained_weights, teacher_checkpoint_path or "
                             "load_kd_model_checkpoint=True")

        # IF STUDENT PRETRAINED WEIGHTS ARE LOADED, REPLACE HEAD ACCORDING TO TEACHER IF NEEDED
        if student_pretrained_weights:
            load_pretrained_weights(student_net, student_architecture, student_pretrained_weights)
            if student_arch_params.num_classes != teacher_arch_params.num_classes:
                student_net.replace_head(new_num_classes=teacher_arch_params.num_classes)

        architecture = KDModule(student=student_net,
                                teacher=teacher_net,
                                run_teacher_on_eval=run_teacher_on_eval)

        super(KDModel, self).build_model(architecture=architecture,
                                         arch_params=kd_model_arch_params,
                                         load_checkpoint=load_kd_model_checkpoint,
                                         source_ckpt_folder_name=kd_model_source_ckpt_folder_name,
                                         external_checkpoint_path=kd_model_external_checkpoint_path
                                         )
