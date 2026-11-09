#!/bin/bash
# runs inside the petgraphdb container; imports LDBC SNB csv (already
# header-converted) into database "neo4j" under /data
set -e
POSTFIX=_0_0.csv
neo4j-admin import --database=neo4j --id-type=INTEGER \
  --nodes=Place="/import/static/place${POSTFIX}" \
  --nodes=Organisation="/import/static/organisation${POSTFIX}" \
  --nodes=TagClass="/import/static/tagclass${POSTFIX}" \
  --nodes=Tag="/import/static/tag${POSTFIX}" \
  --nodes=Comment:Message="/import/dynamic/comment${POSTFIX}" \
  --nodes=Forum="/import/dynamic/forum${POSTFIX}" \
  --nodes=Person="/import/dynamic/person${POSTFIX}" \
  --nodes=Post:Message="/import/dynamic/post${POSTFIX}" \
  --relationships=IS_PART_OF="/import/static/place_isPartOf_place${POSTFIX}" \
  --relationships=IS_SUBCLASS_OF="/import/static/tagclass_isSubclassOf_tagclass${POSTFIX}" \
  --relationships=IS_LOCATED_IN="/import/static/organisation_isLocatedIn_place${POSTFIX}" \
  --relationships=HAS_TYPE="/import/static/tag_hasType_tagclass${POSTFIX}" \
  --relationships=HAS_CREATOR="/import/dynamic/comment_hasCreator_person${POSTFIX}" \
  --relationships=IS_LOCATED_IN="/import/dynamic/comment_isLocatedIn_place${POSTFIX}" \
  --relationships=REPLY_OF="/import/dynamic/comment_replyOf_comment${POSTFIX}" \
  --relationships=REPLY_OF="/import/dynamic/comment_replyOf_post${POSTFIX}" \
  --relationships=CONTAINER_OF="/import/dynamic/forum_containerOf_post${POSTFIX}" \
  --relationships=HAS_MEMBER="/import/dynamic/forum_hasMember_person${POSTFIX}" \
  --relationships=HAS_MODERATOR="/import/dynamic/forum_hasModerator_person${POSTFIX}" \
  --relationships=HAS_TAG="/import/dynamic/forum_hasTag_tag${POSTFIX}" \
  --relationships=HAS_INTEREST="/import/dynamic/person_hasInterest_tag${POSTFIX}" \
  --relationships=IS_LOCATED_IN="/import/dynamic/person_isLocatedIn_place${POSTFIX}" \
  --relationships=KNOWS="/import/dynamic/person_knows_person${POSTFIX}" \
  --relationships=LIKES="/import/dynamic/person_likes_comment${POSTFIX}" \
  --relationships=LIKES="/import/dynamic/person_likes_post${POSTFIX}" \
  --relationships=HAS_CREATOR="/import/dynamic/post_hasCreator_person${POSTFIX}" \
  --relationships=HAS_TAG="/import/dynamic/comment_hasTag_tag${POSTFIX}" \
  --relationships=HAS_TAG="/import/dynamic/post_hasTag_tag${POSTFIX}" \
  --relationships=IS_LOCATED_IN="/import/dynamic/post_isLocatedIn_place${POSTFIX}" \
  --relationships=STUDY_AT="/import/dynamic/person_studyAt_organisation${POSTFIX}" \
  --relationships=WORK_AT="/import/dynamic/person_workAt_organisation${POSTFIX}" \
  "--delimiter=|"
echo PET_IMPORT_DONE
